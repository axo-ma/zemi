"""Capture exact OpenAI chat requests for later dataset-cell conversations."""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
from . import env

CHAT_MIME = 'application/vnd.zemi.chat-context+json'
_sources = {}


def register_arsenal(session):
    """Remember provenance, never credentials, for clients in this process."""
    if session.config_path is None:
        return
    path = (Path(__file__).parent / session.config_path.removeprefix('zemi/')
            if session.config_path.startswith('zemi/') else
            session._resolve_zemi_path(session.config_path))
    provenance = getattr(session, '_chat_provenance', None)
    if provenance is None:
        try:
            component_root = env.path.comp.root
        except FileNotFoundError:
            component_root = Path.cwd().resolve()
        provenance = {'config_path': str(path.resolve()),
                      'config_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'component_root': str(component_root)}
        session._chat_provenance = provenance
    for endpoint in session._endpoint_configs.values():
        resolved = session._resolved_endpoints.get(endpoint['name'])
        if resolved is not None:
            endpoint = resolved.config
        for model in endpoint['models']:
            base = endpoint.get('base_url')
            remote = model['model']
            if not isinstance(base, str) or not isinstance(remote, str):
                continue
            _sources[(base.rstrip('/').removesuffix('/v1'), remote)] = {
                **provenance,
                'endpoint_name': endpoint['name'], 'model_name': model['name'],
                'remote_model': model['model'],
            }


def instrument_client(client, config):
    """Preserve client identity and SDK API; capture non-streaming chat calls."""
    if not hasattr(client, 'chat'):
        return
    original = client.chat.completions.create

    @wraps(original)
    def create(*args, **kwargs):
        if not os.environ.get('ZEMI_PLAYBOOK_OUTPUT_DIR') or kwargs.get('stream'):
            return original(*args, **kwargs)
        request = {}
        unsupported = []
        for key, value in kwargs.items():
            if key in {'extra_headers', 'extra_query'}:
                if value:
                    unsupported.append(key)
                continue
            try:
                request[key] = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
            except (TypeError, ValueError):
                if type(value).__name__ not in {'NotGiven', 'Omit'}:
                    unsupported.append(key)
                continue
        source = _sources.get((config.openai_url.rstrip('/').removesuffix('/v1'), request.get('model')))
        context = {'schema_version': 1, 'request': request, 'source': deepcopy(source),
                   'unsupported_fields': unsupported}
        try:
            result = original(*args, **kwargs)
        except Exception:
            publish(context)
            raise
        if getattr(result, 'choices', None):
            context['assistant'] = result.choices[0].message.model_dump(exclude_none=True)
        publish(context)
        return result

    client.chat.completions.create = create


def publish(context):
    from IPython.display import display
    display({CHAT_MIME: context, 'text/plain': 'ZEMI: conversation context saved.'}, raw=True)


def notebook_contexts(notebook):
    if notebook is None:
        return []
    return [deepcopy(output['data'][CHAT_MIME])
            for cell in notebook['cells'] for output in cell.get('outputs', [])
            if CHAT_MIME in output.get('data', {})]
