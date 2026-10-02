"""Stable stdio transport; reload the implementation on every request."""
import argparse
import json
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def dispatch(method, params, config):
    backend = Path(__file__).with_name('backend.py')
    namespace = runpy.run_path(str(backend), init_globals={'CONFIG_PATH': Path(config)})
    return namespace['handle'](method, params)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if 'id' not in request:
                continue
            result = dispatch(request['method'], request.get('params', {}), args.config)
            response = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except Exception as error:
            response = {'jsonrpc': '2.0', 'id': request.get('id') if isinstance(request, dict) else None,
                        'error': {'code': -32603, 'message': str(error)}}
        print(json.dumps(response), flush=True)


if __name__ == '__main__':
    main()
