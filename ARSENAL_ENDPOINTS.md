# Arsenal endpoints and `arsenal.env`

`[[arsenal.endpoints]]` is the first-class endpoint configuration. `name` is a
stable ZEMI name, while `model` is the real provider model ID. Legacy
`[[arsenal.llamas]]`, Model Mode and Router Mode remain supported and normalize
to managed llama.cpp endpoints.

## Persistent values

Arsenal reads user-entered values only from:

```text
@inst/_secrets/arsenal.env
```

The rule is deliberately simple: if the value exists and validates, use it; if
not, ask with `input()` or, for `secret = true`, `getpass.getpass()`, validate,
save, and use it. This is identical in a terminal and an interactive notebook.
There is no fallback to process/system environment variables and the file is
not loaded into `os.environ`.

```toml
api_key = { env = "OPENROUTER_API_KEY", prompt = "Введите API-ключ OpenRouter", secret = true }
base_url = { env = "HOST_LLM_BASE_URL", prompt = "Введите URL локальной модели на хосте", suggested = "http://host.docker.internal:8080/v1", validate = "url" }
model = { env = "HOST_LLM_MODEL", prompt = "Введите model ID внешнего сервера", validate = "non_empty" }
```

Pressing Enter accepts `suggested`. Invalid values are requested again; EOF or
cancel produces a finite, explicit error. To rotate a value or force the next
session to ask again, safely remove only its `NAME=value` line from
`arsenal.env`. Existing clients retain the value already read; a new session or
client reads the file again.

The UTF-8 dotenv parser does not execute shell code. It validates names, rejects
duplicates and malformed lines, quotes special values, preserves comments and
unrelated keys, locks concurrent updates, and replaces the file atomically.
Windows ACL restriction is best effort and emits a warning if it cannot be
applied. Never add this file to a component, template, Git, run artifact, or
report.

## Managed and external lifecycle

- `managed`: Arsenal downloads/prepares GGUF and llama.cpp lazily and owns only
  the process started by the current `ArsenalSession`.
- `external`: Arsenal resolves connection values lazily, validates an existing
  endpoint and creates clients. It never starts or stops that endpoint.

`end()` cannot terminate an external server. Managed cleanup does not kill an
unowned `llama-server` merely because it listens on the configured port.

```python
session.endpoints.openrouter.models.openrouter_model
session.endpoints.host_llm.models.host_model
```

For a host service visible from a VM, WSL or Docker, use
`llm_external_local.toml` and enter the reachable URL when first requested.
`http://host.docker.internal:8080/v1` is offered only as a suggestion; Arsenal
does not guess the correct host route.

## Validation and integrations

Healthchecks are `none`, `tcp`, and OpenAI-compatible `models`. Positive
`connect_timeout` and `request_timeout` are mandatory; model validation can be
disabled for providers that do not expose `GET /models`. Diagnostics distinguish
bad URLs, DNS, refused connections, timeout, authentication, and missing model
without including keys.

OpenAI-compatible endpoints support OpenAI, LiteLLM, DSPy, Instructor,
PydanticAI, Smolagents, LlamaIndex, HTTPX, Outlines, and Guidance. Connection
configuration is passed directly; LiteLLM is not a required gateway. Native
Anthropic is represented as a separate protocol but its client adapter remains
an explicit next-stage limitation: requesting an OpenAI integration raises a
contextual unsupported-protocol error.

See `ARSENAL_MODELS.md` for the models and logical names in every tracked
Arsenal TOML.
