"""Deterministic Review Reports with launch-time provenance and prompt snapshots."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from . import env
from .dataset import zemi_path
from .reporting import _cell, _table, _summary_params


def _git(directory, *args):
    result = subprocess.run(['git', '-c', f'safe.directory={Path(directory).as_posix()}', '-C', str(directory), *args], capture_output=True,
                            encoding='utf-8', timeout=15)
    return result.stdout.strip() if result.returncode == 0 else None


def capture_review(component, module, dataset):
    """Collect launch provenance solely from resolved configuration."""
    import hashlib
    import tomllib
    from .params import ParamSpace
    from .prompting import validate_binding, load_prompts

    entry = Path(sys.argv[0]).resolve()
    if not entry.is_file() or not entry.is_relative_to(component.root):
        entry = None
    paths = [component.params_path, module.source_path]
    if entry is not None:
        paths.append(entry)
    configuration, prompts = {}, {}
    prompt_files = {}
    space = ParamSpace(config=module.config['_v05_space'])
    for sample in space.grid():
        binding = sample.values.get('encoding_prompt')
        if binding is None:
            continue
        validate_binding(binding)
        name, file = binding['prompt_name'], binding['prompt_file']
        if name in prompt_files and prompt_files[name] != file:
            raise ValueError(f'Prompt name {name} refers to different files')
        prompt_files[name] = file
        prompts[name] = load_prompts(file)[name]
        paths.extend([zemi_path(file), zemi_path(binding['encoder'].rsplit(':', 1)[0])])
    optimizer = module.optimizer_config
    dataset_ref = optimizer.get('trial_dataset', {}).get('path')
    if dataset_ref:
        paths.append(zemi_path(dataset_ref))
        configuration['Dataset'] = dataset_ref
    trial_type = optimizer.get('sample_trial', {}).get('type')
    if trial_type:
        configuration['SampleTrial'] = trial_type
        if trial_type.startswith(('@comp/', '@inst/')):
            paths.append(zemi_path(trial_type.rsplit(':', 1)[0]))
    config_path = module.params.get('arsenal_config_path')
    if config_path and zemi_path(config_path).is_file():
        config_file = zemi_path(config_path)
        paths.append(config_file)
        config = tomllib.loads(config_file.read_text(encoding='utf-8'))
        for server in config.get('arsenal', {}).get('llamas', []):
            for model in server.get('models', []):
                if model.get('name') == module.params.get('model_name'):
                    if all(model.get(k) for k in ('owner', 'repository', 'filename')):
                        configuration['Model'] = f"hf:{model['owner']}/{model['repository']}/{model['filename']}"
                    configuration.update({'Runtime': server.get('llama_build'),
                        'Context size': model.get('ctx_size'), 'Inference threads': model.get('threads'),
                        'Reasoning': model.get('reasoning')})
    for key, label in (('model_name', 'Model alias'), ('temperature', 'Temperature'), ('max_tokens', 'Maximum output tokens')):
        if key in module.params:
            configuration[label] = module.params[key]

    def relative(file):
        file = Path(file).resolve()
        if file.is_relative_to(component.root):
            return file.relative_to(component.root).as_posix()
        return '@inst/' + file.relative_to(env.path.inst).as_posix()

    files = {relative(p): p.read_text(encoding='utf-8') for p in dict.fromkeys(paths) if p.is_file()}
    data_files, directories = {}, [component.root, component.root / 'zemi']
    for item in dataset.items:
        ref = item.get('input', {}).get('workbook_path')
        if ref:
            file = zemi_path(ref)
            data_files[ref] = hashlib.sha256(file.read_bytes()).hexdigest()
            directory = _git(file.parent, 'rev-parse', '--show-toplevel')
            if directory:
                directories.append(Path(directory))
    repos = []
    for directory in dict.fromkeys(directories):
        directory = Path(directory).resolve()
        if not directory.is_dir():
            continue
        status = _git(directory, 'status', '--porcelain')
        repos.append({'directory': directory.relative_to(env.path.inst).as_posix(),
                      'remote': _git(directory, 'remote', 'get-url', 'origin'),
                      'commit': _git(directory, 'rev-parse', 'HEAD'),
                      'dirty': bool(status) if status is not None else None})
    python = Path(sys.executable).resolve().relative_to(env.path.inst).as_posix()
    return {'schema_version': 2, 'entrypoint': relative(entry) if entry else None,
            'component_directory': component.root.relative_to(env.path.inst).as_posix(),
            'python': python, 'params_file': relative(component.params_path),
            'playbook': module.playbook_name, 'optimizer': optimizer,
            'configuration': configuration, 'prompts': prompts, 'sources': files,
            'data_files': data_files, 'repositories': repos}


def _fence(text, language='text'):
    longest = max((len(m.group()) for m in re.finditer(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}{language}\n{text}\n{fence}'


def render_review(snapshot, *, samples, report, module_id, writer, item_count=None):
    runs = [r for s in samples for r in s.get('runs', [])]
    settings = [('Run', writer.root.name), ('Status', report['status']),
                ('Started', report.get('started_at')), ('Finished', report.get('finished_at')),
                ('Job', snapshot['entrypoint']), ('Parameters', snapshot['params_file']),
                ('Playbook', snapshot['playbook']), *snapshot['configuration'].items(),
                ('Optimizer', snapshot['optimizer'].get('strategy')),
                ('Maximum samples', snapshot['optimizer'].get('max_trials')),
                ('Reuse kernel', snapshot['optimizer'].get('reuse_kernel', True)),
                ('Dataset items', item_count), ('Samples', len(samples)),
                ('Successful runs / Total', f"{sum(r.get('status') == 'succeeded' for r in runs)} / {len(runs)}")]
    settings.extend((f"Commit: {repo['directory']}", repo['commit']) for repo in snapshot['repositories'])
    settings.extend((f"Dirty at launch: {repo['directory']}", repo['dirty']) for repo in snapshot['repositories'])
    commands = ['# Run from the ZEMI Instance root.']
    for repo in snapshot['repositories']:
        # Submodule revisions are pinned by the component repository.
        if repo['directory'].startswith(snapshot['component_directory'] + '/'):
            continue
        if repo['remote'] and repo['commit']:
            commands.extend([f"git clone '{repo['remote']}' '{repo['directory']}'",
                             f"git -C '{repo['directory']}' checkout {repo['commit']}",
                             f"git -C '{repo['directory']}' submodule update --init --recursive"])
    commands += [f"cd '{snapshot['component_directory']}'", 'python 00_init.py']
    if snapshot['entrypoint']:
        commands.append(f"& '../{snapshot['python']}' '{snapshot['entrypoint']}'")
    else:
        code = (f"from zemi.component import ZemiComponent; c = ZemiComponent('@comp/{snapshot['params_file']}'); "
                "exec('try:\\n    c.run()\\nfinally:\\n    c.close()')")
        commands.append(f'& "../{snapshot["python"]}" -c "{code}"')
    rows = []
    for number, sample in enumerate(samples, 1):
        sruns = sample.get('runs', [])
        def mean(key):
            values = [(r.get('prediction') or {}).get(key) for r in sruns]
            values = [v for v in values if isinstance(v, (float, int)) and not isinstance(v, bool)]
            return sum(values) / len(values) if values else None
        sample_id = sample.get('sample_trial_id') or sample.get('sample_id') or sample.get('id') or f'Sample {number}'
        label = str(sample_id)
        if sample.get('params'):
            label += ': ' + json.dumps(_summary_params(sample['params']), ensure_ascii=False, sort_keys=True)
        rows.append((label, sample.get('score'), mean('item_tokens'), mean('prompt_tokens'),
                     sum(bool(r.get('evaluation_error')) for r in sruns)))
    parts = ['## Run configuration', _table(('Setting', 'Value'), settings), '## Reproduction',
             'The instance requires the configured Python environment, model and runtime. '
             'Commits describe the checkout at launch; dirty checkouts additionally require the saved source snapshot.',
             _fence('\n'.join(commands), 'powershell'), '## Results',
             _table(('Sample / parameters', 'Score', 'Mean item tokens', 'Mean prompt tokens', 'Evaluator errors'), rows),
             'Score is the SampleTrial score. Token means use available numeric outputs; unavailable values are shown as —.',
             '## Prompts and examples']
    for name, prompt in snapshot['prompts'].items():
        parts += [f'### {name}', _fence(prompt)]
    if not snapshot['prompts']:
        parts.append('No prompt templates supplied.')
    snapshot_file = Path(writer.ref('review', module_id).path).with_suffix('.json').name
    parts += ['## Source snapshot', 'Launch-time source text and provenance are saved in '
              f'[{snapshot_file}]({snapshot_file}).']
    return '\n\n'.join(parts)
