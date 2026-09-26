"""Load named Markdown prompts and explicitly paired component encoders."""
from __future__ import annotations

import re
from collections.abc import Mapping
from .dataset import zemi_path, resolve_adapter


def load_prompts(path):
    """Read # name sections; headings inside fenced code are literal text."""
    prompts, name, lines, fence = {}, None, [], None
    def finish():
        if name is not None:
            text = ''.join(lines).strip('\n')
            if text.count('{{item}}') != 1:
                raise ValueError(f'Prompt {name!r} requires exactly one {{{{item}}}} placeholder')
            prompts[name] = text
    for line in zemi_path(path).read_text(encoding='utf-8').splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\n'))
        heading = re.fullmatch(r'# ([A-Za-z0-9][A-Za-z0-9_.-]*)\s*', line) if fence is None else None
        if heading:
            finish()
            name, lines = heading[1], []
            if name in prompts:
                raise ValueError(f'Duplicate prompt name: {name}')
        elif name is not None:
            lines.append(line)
        elif line.strip():
            raise ValueError('prompts.md must begin with a # prompt_name section')
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
    if fence is not None:
        raise ValueError('Unclosed code fence in prompts.md')
    finish()
    if not prompts:
        raise ValueError('No named prompts in prompts.md')
    return prompts


def validate_binding(binding):
    required = ('prompt_name', 'prompt_file', 'encoder', 'encoding_format')
    if not isinstance(binding, Mapping) or set(binding) != set(required):
        raise ValueError(f'encoding_prompt requires {required}')
    if any(not isinstance(binding[k], str) or not binding[k] for k in required):
        raise ValueError('encoding_prompt fields must be nonempty strings')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', binding['prompt_name']):
        raise ValueError('Invalid prompt_name')
    if binding['prompt_name'] not in load_prompts(binding['prompt_file']):
        raise ValueError(f"Missing prompt: {binding['prompt_name']}")
    filename, separator, function = binding['encoder'].rpartition(':')
    if not separator or not re.fullmatch(r'@comp/[^:]+\.py', filename) or not function.isidentifier():
        raise ValueError('encoder must be @comp/file.py:function')
    if not zemi_path(filename).is_file():
        raise FileNotFoundError(filename)
    return dict(binding)


def build_prompt(binding, workbook_path, worksheet_name):
    """Return encoded item text and the rendered prompt without model calls."""
    binding = validate_binding(binding)
    encoder = resolve_adapter('encoder', binding['encoder'])
    item = encoder(zemi_path(workbook_path), worksheet_name, format=binding['encoding_format'])
    if not isinstance(item, str):
        raise TypeError('Encoder must return text')
    template = load_prompts(binding['prompt_file'])[binding['prompt_name']]
    return item, template.replace('{{item}}', item)


def sample_names(space):
    """Stable names assigned by configured grid order, independently per prompt."""
    counters, names = {}, {}
    for sample in space.grid():
        binding = sample.values.get('encoding_prompt')
        if binding is None:
            continue
        validate_binding(binding)
        name = binding['prompt_name']
        counters[name] = counters.get(name, 0) + 1
        names[sample.key()] = f'{name}-{counters[name]:03d}'
    return names
