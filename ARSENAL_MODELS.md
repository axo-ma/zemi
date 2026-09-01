# Модели в ZEMI Arsenal

Этот файл описывает фактические Arsenal-конфигурации, которые находятся рядом
с ним в библиотеке `zemi`. Логическое имя из столбца `name` используется для
доступа из Python; `alias` — имя модели, передаваемое серверу llama.cpp.

## `llm_curated_set_model_mode.toml`

Managed Arsenal в Model Mode. Каждая модель запускается собственным процессом
llama.cpp на отдельном порту.

| `name` | `alias` |
|---|---|
| `qwen35_4b` | `qwen3.5-4b` |
| `lfm2_2_6b` | `lfm2-2.6b` |
| `phi35_mini` | `phi-3.5-mini` |
| `llama32_3b` | `llama-3.2-3b` |
| `qwen25_coder_3b` | `qwen2.5-coder-3b-instruct` |
| `qwen3_1_7b` | `qwen3-1.7b` |
| `smollm2_1_7b` | `smollm2-1.7b` |
| `qwen3_4b` | `qwen3-4b-instruct-2507` |
| `lfm25_8b_a1b` | `lfm2.5-8b-a1b` |
| `lfm2_350m` | `lfm2-350m` |
| `lfm2_700m` | `lfm2-700m` |
| `lfm2_1_2b` | `lfm2-1.2b` |
| `ling30_tiny` | `ling-3.0-tiny` |

## `llm_curated_set_router_mode.toml`

Managed Arsenal в Router Mode. Один `curated_router` предоставляет следующий
набор моделей:

| `name` | `alias` |
|---|---|
| `qwen35_4b` | `qwen3.5-4b` |
| `ling30_tiny` | `ling-3.0-tiny` |
| `lfm2_2_6b` | `lfm2-2.6b` |
| `phi35_mini` | `phi-3.5-mini` |
| `llama32_3b` | `llama-3.2-3b` |
| `qwen25_coder_3b` | `qwen2.5-coder-3b-instruct` |
| `qwen3_1_7b` | `qwen3-1.7b` |
| `smollm2_1_7b` | `smollm2-1.7b` |
| `qwen3_4b` | `qwen3-4b-instruct-2507` |
| `lfm25_8b_a1b` | `lfm2.5-8b-a1b` |
| `lfm2_350m` | `lfm2-350m` |
| `lfm2_700m` | `lfm2-700m` |
| `lfm2_1_2b` | `lfm2-1.2b` |

В Router Mode отсутствует только `ling30_tiny` как отдельный сервер: она
доступна внутри общего `curated_router`.

## Внешние и демонстрационные конфигурации

Следующие TOML являются примерами подключения и не закрепляют готовую модель:

| Arsenal TOML | Endpoint | Логическое имя модели | Фактическая модель |
|---|---|---|---|
| `llm_external_local.toml` | `host_llm` | `host_model` | ссылка `HOST_LLM_MODEL` в `arsenal.env` |
| `llm_external_providers.toml` | `openrouter` | `openrouter_model` | ссылка `OPENROUTER_MODEL` |
| `llm_external_providers.toml` | `openrouter_free` | `openrouter_free` | `openrouter/free` |
| `llm_external_providers.toml` | `openai` | `openai_model` | ссылка `OPENAI_MODEL` |
| `llm_external_providers.toml` | `anthropic` | `anthropic_model` | ссылка `ANTHROPIC_MODEL` (native client пока не реализован) |
| `llm_external_providers.toml` | `gemini` | `gemini_model` | ссылка `GEMINI_MODEL` |
| `llm_external_providers.toml` | `groq` | `groq_model` | ссылка `GROQ_MODEL` |
| `llm_external_providers.toml` | `deepseek` | `deepseek_model` | ссылка `DEEPSEEK_MODEL` |
| `llm_external_providers.toml` | `kimi` | `kimi_model` | ссылка `MOONSHOT_MODEL` |
| `llm_external_providers.toml` | `xai` | `xai_model` | ссылка `XAI_MODEL` |
| `llm_external_providers.toml` | `mistral` | `mistral_model` | ссылка `MISTRAL_MODEL` |

Ссылки разрешаются лениво: при создании сессии значения не запрашиваются.
Первый доступ к выбранной модели читает либо дополняет
`@inst/_secrets/arsenal.env`.

## Доступ из Python

Для legacy managed-конфигураций сохраняется доступ через `llamas`:

```python
model = arsenal.llamas.curated_router.models.qwen35_4b
```

Через объектную модель endpoint’ов та же схема имеет вид:

```python
model = arsenal.endpoints.curated_router.models.qwen35_4b
```

Для внешнего локального примера:

```python
model = arsenal.endpoints.host_llm.models.host_model
```
