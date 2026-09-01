# Arsenal endpoints

`[[arsenal.endpoints]]` is the primary configuration. `name` is ZEMI's stable
logical name; a model's `model` value is the exact id or alias sent to the
remote API. Endpoint and model names are globally unique, so both full and flat
access are unambiguous:

```python
session.endpoints.host_llama.models.host_model
session.models["host_model"]
session.check("host_llama", "host_model")
```

`kind = "managed"` means the session may prepare artifacts and start llama.cpp.
It stops only a process started by that same `ArsenalSession`; a listener merely
found on the configured port is never terminated. Its URL is derived solely
from `runtime.host` and `runtime.port`, so `base_url` is forbidden.

`kind = "external"` accepts an existing endpoint and never starts or stops it.
The ZEMI Instance supplies VM/WSL/Docker or remote host routing through
`base_url`, normally `${ZEMI_HOST_LLM_BASE_URL}`. Arsenal does not guess a host
address. `begin()` remains lazy; access to an external model performs its
configured check, or `check()`/`validate()` can do so explicitly.

Healthchecks are `none`, TCP connection only (`tcp`), or OpenAI model discovery
(`models`). Set `validate_model = false` when `/v1/models` is unavailable or
restricted. Positive `connect_timeout` controls checks; positive
`request_timeout` is passed to clients.

Keys use `api_key_env`; tracked TOML must not contain secrets. `${ENV_NAME}` is
supported in `base_url`, and a missing or empty variable is an error. Resolved
diagnostic configuration and connection repr redact keys.

Protocol `openai` supports OpenAI, LiteLLM, DSPy, Instructor, PydanticAI,
Smolagents, LlamaIndex, HTTPX, Outlines, and Guidance adapters. Provider metadata
(`openrouter`, `openai`, `deepseek`, `kimi`, or custom) is carried independently
from transport and does not select provider-specific classes. `anthropic` is a
validated protocol boundary, but native clients are intentionally not yet
implemented: requesting an existing adapter raises an explicit unsupported
error rather than silently translating Anthropic semantics.

Legacy `[[arsenal.llamas]]` remains supported and is strictly normalized to
managed llama.cpp endpoints internally. The curated Model and Router Mode files
therefore do not require immediate migration.
