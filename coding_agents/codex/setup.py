"""Register the verified direct MCP integration with one command."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from zemi import env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codex', default=shutil.which('codex'))
    parser.add_argument('--component', required=True, type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--excel', type=Path)
    parser.add_argument('--item', type=int, default=1)
    parser.add_argument('--sample', type=int, default=1)
    args = parser.parse_args()
    if not args.codex:
        parser.error('Codex CLI is not on PATH; supply --codex with its executable path')
    component = args.component.resolve()
    if not (component / '.zemicomp').is_file():
        parser.error('Component must contain the .zemicomp marker')
    if args.item < 1 or args.sample < 1:
        parser.error('Item and sample numbers start at 1')
    for file in (args.manifest, args.excel, component / 'zemi/report_viewer.py'):
        if file is None:
            continue
        if not file.is_file():
            parser.error(f'File does not exist: {file}')
    env.path.tmp.mkdir(parents=True, exist_ok=True)
    config = env.path.tmp / 'zemi-codex-integration.json'
    settings = json.loads(config.read_text(encoding='utf-8')) if config.is_file() else {}
    settings['component'] = str(component)
    if args.manifest:
        settings.update(manifest=str(args.manifest.resolve()), item=args.item, sample=args.sample)
    if args.excel:
        settings['excel'] = str(args.excel.resolve())
    config.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    subprocess.run([args.codex, 'mcp', 'add', 'zemi_probe', '--', sys.executable,
                    str(Path(__file__).with_name('server.py')), '--config', str(config)], check=True)
    print('Integration registered. Restart Codex once, then call zemi_report_show with a report path.')


if __name__ == '__main__':
    main()
