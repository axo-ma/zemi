"""Terminal continuation of a captured dataset-item/model conversation."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import time
import uuid

CHAT_CONTEXT_SIZE = 32768


def create_input_session(*, input=None, output=None):
    """Keep a pasted block in one message while Enter submits typed text."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings

    bindings = KeyBindings()

    @bindings.add('enter')
    def submit(event):
        event.current_buffer.validate_and_handle()

    @bindings.add('escape', 'enter')
    def newline(event):
        event.current_buffer.insert_text('\n')

    return PromptSession(multiline=True, key_bindings=bindings, input=input, output=output)


def load_context(manifest, row, column):
    data = json.loads(Path(manifest).read_text(encoding='utf-8'))
    cell = data['rows'][row]['cells'][column]
    contexts = cell.get('contexts', [])
    if len(contexts) != 1:
        raise ValueError('This cell has no unique captured chat request. '
                         'Capture requires a non-streaming Arsenal OpenAI chat call; old reports cannot restore it.')
    context = contexts[0]
    if context.get('unsupported_fields'):
        raise ValueError('Cannot exactly restore request fields: ' + ', '.join(context['unsupported_fields']))
    if not context.get('source'):
        raise ValueError('Arsenal configuration provenance was not captured for this request.')
    request = deepcopy(context['request'])
    if not isinstance(request.get('messages'), list) or not request['messages']:
        raise ValueError('Original messages are missing.')
    if request.get('n', 1) != 1:
        raise ValueError('Multiple response choices cannot be continued unambiguously.')
    if context.get('assistant'):
        assistant = deepcopy(context['assistant'])
        if assistant.get('tool_calls') or assistant.get('function_call'):
            raise ValueError('This response needs tool execution before a chat can continue.')
        request['messages'].append(assistant)
    return data['rows'][row]['item_id'], cell, context['source'], request


def prepare_session(source, context_size=CHAT_CONTEXT_SIZE):
    from .arsenal.runtime import ArsenalSession
    from .arsenal.config import normalize_endpoints
    from . import toml
    os.chdir(source['component_root'])
    os.environ.pop('ZEMI_PLAYBOOK_OUTPUT_DIR', None)
    config_path = Path(source['config_path'])
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != source['config_sha256']:
        raise ValueError('Arsenal configuration changed since the run. Restore the original file to continue with the same settings.')
    original = toml.load(config_path)
    endpoint = next(e for e in normalize_endpoints(original['arsenal'])
                    if e['name'] == source['endpoint_name'])
    endpoint = deepcopy(endpoint)
    endpoint.pop('_legacy_name', None)
    model = next(m for m in endpoint['models'] if m['name'] == source['model_name'])
    endpoint['models'] = [model]
    if endpoint['kind'] == 'external' and endpoint.get('api_key') == '':
        endpoint.pop('api_key')
    if endpoint['kind'] == 'managed':
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        endpoint['runtime'].update(host='127.0.0.1', port=port)
        model['runtime']['ctx_size'] = max(int(model['runtime']['ctx_size']), context_size)
        model['context_window'] = model['runtime']['ctx_size']
        endpoint.pop('base_url', None)
    session = ArsenalSession({'arsenal': {'mode': 'model', 'endpoints': [endpoint]}})
    return session, endpoint['name'], model['name'], endpoint['kind']


def continue_chat(client, request, message, *, on_token=None, stats=None):
    """Stream only when requested; failures never change the stored conversation."""
    candidate = deepcopy(request)
    candidate['messages'].append({'role': 'user', 'content': message})
    candidate.pop('stream_options', None)
    started = time.perf_counter()
    usage = None
    if on_token is None:
        candidate['stream'] = False
        response = client.chat.completions.create(**candidate)
        answer = response.choices[0].message.model_dump(exclude_none=True)
        usage = getattr(response, 'usage', None)
    else:
        outgoing = deepcopy(candidate)
        outgoing.update(stream=True, stream_options={'include_usage': True})
        response = client.chat.completions.create(**outgoing)
        parts, reasoning, refusal = [], [], []
        finished = False
        try:
            for chunk in response:
                if getattr(chunk, 'usage', None) is not None:
                    usage = chunk.usage
                for choice in chunk.choices:
                    if choice.index != 0:
                        raise ValueError('Multiple response choices cannot be continued.')
                    delta = choice.delta
                    if getattr(delta, 'tool_calls', None) or getattr(delta, 'function_call', None):
                        raise ValueError('The model requested a tool; this terminal does not execute tools.')
                    text = getattr(delta, 'content', None)
                    if text:
                        parts.append(text)
                        on_token(text)
                    if getattr(delta, 'reasoning_content', None):
                        reasoning.append(delta.reasoning_content)
                    if getattr(delta, 'refusal', None):
                        refusal.append(delta.refusal)
                    if choice.finish_reason is not None:
                        finished = True
            if not finished:
                raise RuntimeError('Response stream ended before completion; conversation was not changed.')
        finally:
            response.close()
        answer = {'role': 'assistant', 'content': ''.join(parts)}
        if reasoning:
            answer['reasoning_content'] = ''.join(reasoning)
        if refusal:
            answer['refusal'] = ''.join(refusal)
        candidate['stream'] = False
    if answer.get('tool_calls') or answer.get('function_call'):
        raise ValueError('The model requested a tool; this terminal does not execute tools.')
    candidate['messages'].append(answer)
    request.clear()
    request.update(candidate)
    if stats is not None:
        stats.update(seconds=time.perf_counter() - started,
                     completion_tokens=getattr(usage, 'completion_tokens', None))
    return answer.get('content') or ''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('row', type=int)
    parser.add_argument('column', type=int)
    parser.add_argument('--context-size', type=int, default=CHAT_CONTEXT_SIZE)
    args = parser.parse_args(argv)
    if args.context_size <= 0:
        parser.error('--context-size must be positive')
    session = client = None
    from .arsenal.process_owner import ProcessOwner
    owner = ProcessOwner()
    try:
        item, cell, source, request = load_context(args.manifest, args.row, args.column)
        print(f'ZEMI chat · {item} · {cell["sample_id"]}')
        print('Original answer:', request['messages'][-1].get('content', '(no answer)'))
        session, endpoint_name, model_name, kind = prepare_session(source, args.context_size)
        session._begin(stop_arsenal_before_begin=False)
        model = session.endpoints[endpoint_name].models[model_name]
        if model.config['model'] != request['model']:
            raise ValueError('Resolved model identifier changed since the original request.')
        if kind == 'managed':
            owner.attach(session._processes[endpoint_name])
        from .arsenal.libs import Libs
        endpoint = session.endpoints[endpoint_name]
        client = Libs(model._base_url, model=model.config['model'],
                      api_key=endpoint.config.get('api_key', 'llama.cpp'),
                      timeout=endpoint.config['request_timeout'],
                      headers=endpoint.config.get('headers'), exact_base_url=kind == 'external').openai.client
        print(f'Context: {model.config.get("context_window", "provider limit")} tokens. '
              'Paste a multiline request directly; Enter sends; Alt+Enter adds a line; /exit closes.')
        input_session = create_input_session()
        transcript = args.manifest.resolve().parent / 'chats' / (uuid.uuid4().hex + '.json')
        transcript.parent.mkdir(parents=True, exist_ok=True)
        print('Conversation saved separately:', transcript)
        while True:
            try:
                message = input_session.prompt('\nВы > ')
            except EOFError:
                break
            if message.strip() == '/exit':
                break
            if not message.strip():
                continue
            try:
                print('Обработка запроса…')
                print('\nМодель > ', end='', flush=True)
                stats = {}
                continue_chat(client, request, message,
                              on_token=lambda text: print(text, end='', flush=True), stats=stats)
                tokens = stats['completion_tokens']
                count = f'{tokens} токенов' if tokens is not None else 'число токенов не сообщено'
                print(f"\nОтвет: {count} · {stats['seconds']:.1f} с")
            except Exception as error:
                print(f'\nRequest failed: {error}')
                continue
            saved = {'item_id': item, 'sample_id': cell['sample_id'], 'source': source,
                     'request': request, 'context_size': model.config.get('context_window')}
            temporary = transcript.with_suffix('.tmp')
            temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, transcript)
    except KeyboardInterrupt:
        print('\nChat closed.')
    except Exception as error:
        print(f'Cannot open conversation: {error}')
        input('Press Enter to close…')
        return 1
    finally:
        if client is not None:
            client.close()
        if session is not None:
            session._end(stop_arsenal_after_end=True)
        owner.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
