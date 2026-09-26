"""Deterministic Review Reports with launch-time provenance and prompt snapshots."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from . import env
from .dataset import zemi_path
from .reporting import _cell, _table


def _git(directory, *args):
    result = subprocess.run(['git', '-c', f'safe.directory={Path(directory).as_posix()}', '-C', str(directory), *args], capture_output=True,
                            encoding='utf-8', timeout=15)
    return result.stdout.strip() if result.returncode == 0 else None


def capture_review(component, module, *, entrypoint, settings, prompts, sources, repositories):
    entry = zemi_path(entrypoint).resolve()
    paths = [component.params_path, module.source_path, entry, *(zemi_path(p) for p in sources)]
    files = {p.relative_to(component.root).as_posix(): p.read_text(encoding='utf-8')
             for p in dict.fromkeys(paths)}
    repos = []
    for directory in dict.fromkeys([component.root, component.root / 'zemi', *(zemi_path(p) for p in repositories)]):
        directory = Path(directory).resolve()
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        status = _git(directory, 'status', '--porcelain')
        repos.append({'directory': directory.relative_to(env.path.inst).as_posix(),
                      'remote': _git(directory, 'remote', 'get-url', 'origin'),
                      'commit': _git(directory, 'rev-parse', 'HEAD'),
                      'dirty': bool(status) if status is not None else None})
    python = Path(sys.executable).resolve().relative_to(env.path.inst).as_posix()
    return {'schema_version': 1, 'entrypoint': entry.relative_to(component.root).as_posix(),
            'component_directory': component.root.relative_to(env.path.inst).as_posix(),
            'python': python, 'params_file': component.params_path.relative_to(component.root).as_posix(),
            'playbook': module.playbook_name, 'optimizer': module.optimizer_config,
            'settings': settings, 'prompts': prompts, 'sources': files, 'repositories': repos}


def _fence(text, language='text'):
    longest = max((len(m.group()) for m in re.finditer(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}{language}\n{text}\n{fence}'


def render_review(snapshot, *, samples, report, module_id, writer, item_count=None):
    runs = [r for s in samples for r in s.get('runs', [])]
    settings = [('Run', writer.root.name), ('Status', report['status']),
                ('Started', report.get('started_at')), ('Finished', report.get('finished_at')),
                ('Job', snapshot['entrypoint']), ('Parameters', snapshot['params_file']),
                ('Playbook', snapshot['playbook']), *snapshot['settings'].items(),
                ('Optimizer', snapshot['optimizer'].get('strategy')),
                ('Maximum samples', snapshot['optimizer'].get('max_trials')),
                ('Reuse kernel', snapshot['optimizer'].get('reuse_kernel', True)),
                ('Worksheets', item_count), ('Samples', len(samples)),
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
    commands += [f"cd '{snapshot['component_directory']}'", 'python 00_init.py',
                 f"& '../{snapshot['python']}' '{snapshot['entrypoint']}'"]
    rows = []
    for number, sample in enumerate(samples, 1):
        sruns = sample.get('runs', [])
        def mean(key):
            values = [(r.get('prediction') or {}).get(key) for r in sruns]
            values = [v for v in values if isinstance(v, (float, int)) and not isinstance(v, bool)]
            return sum(values) / len(values) if values else None
        label = sample.get('params', {}).get('encoding_format') or f'Sample {number}'
        rows.append((label, sample.get('score'), mean('item_tokens'), mean('prompt_tokens'),
                     sum(bool(r.get('evaluation_error')) for r in sruns)))
    parts = ['## Run configuration', _table(('Setting', 'Value'), settings), '## Reproduction',
             'The instance requires the configured Python environment, model and runtime. '
             'Commits describe the checkout at launch; dirty checkouts additionally require the saved source snapshot.',
             _fence('\n'.join(commands), 'powershell'), '## Results',
             _table(('Encoding and prompt', 'Score', 'Mean item tokens', 'Mean prompt tokens', 'Evaluator errors'), rows),
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
