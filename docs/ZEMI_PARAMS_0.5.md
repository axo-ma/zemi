# ZEMI Params 0.5

Params 0.5 is the canonical declarative format for a ZEMI Component.

```toml
[system]
version = "0.5"

[component]
name = "table-search"
stop_on_error = true

[[arsenals]]
id = "local"
config_path = "@comp/zemi/llm_curated_set_model_mode.toml"
lifecycle = "job"

[[playbooks]]
id = "detect"
path = "@comp/detect.ipynb"
arsenal = "local"

[playbooks.params]
model_name = "qwen35_4b"
temperature = { values = [0.0, 0.2], start = 0.0 }
prompt = { values = ["brief", "rules"], start = "brief" }

[playbooks.optimizer]
strategy = "block_coordinate"
max_samples = 4
blocks = [["temperature", "prompt"]]

[playbooks.optimizer.sample_trial]
type = "@comp/zemi/sample_trial.py:TableDetectionSampleTrial"
dataset = "@comp/data/validation.json"

[playbooks.optimizer.sample_trial.params]
strict = true
```

All paths MUST be relative, `@comp/...`, or `@inst/...`; absolute paths and
parent traversal are invalid.

## Structural sections

The only top-level sections are `system`, `component`, `arsenals`, and
`playbooks`. Structural tables are closed. Free-form values belong in a
`params` table.

- `[system]` requires `version = "0.5"` and may contain `[system.params]`.
- `[component]` may contain `name`, `stop_on_error`, and `[component.params]`.
- `[[arsenals]]` may contain `id`, `config_path`, `lifecycle`, and
  `[arsenals.params]`. Arsenal use is optional.
- `[[playbooks]]` requires `id` and `path`; `arsenal` and `enabled` are optional.
  Playbook input parameters live only in `[playbooks.params]`.

## Resolution and ParamSpace

Every Playbook has exactly one resolved `[playbooks.params]` tree. `ref` and
`__include__` may reuse values from `system.params`, `component.params`, an
Arsenal's `params`, or a Playbook's `params`. Resolution is deep-copying,
cycle-checked, and completes before ParamSpace construction.

`select` and `input` are resolved before ParamSpace construction and do not
create optimizer dimensions. The only variable wrappers are:

```toml
temperature = { values = [0.0, 0.2, 0.5], start = 0.2 }
seed = { range = { min = 1, max = 5, step = 1 }, start = 1 }
```

`start` MUST be a member of its finite domain. The public API is
`ParamSpace(config=resolved_playbook_params)`. ParamSpace has no independent
configuration source.

## Fixed and variable Playbooks

A fixed-only Playbook MUST omit `[playbooks.optimizer]` and executes exactly
once as an ordinary PlaybookRun.

A Playbook with variable dimensions MUST define `[playbooks.optimizer]` and its
`sample_trial`. It always enters optimization. There is no `param_space_mode`
and no silent start-only execution.

## PlaybookOptimizer

```python
optimizer = PlaybookOptimizer(config=optimizer_config, param_space=param_space)
sample = optimizer.next_param_sample(history)
best = optimizer.best_param_sample(history)
```

Supported strategies are `grid`, `random`, `coordinate`, and
`block_coordinate`. Non-grid strategies require positive `max_samples`.
`random` may use `seed`. `block_coordinate` requires `blocks`; named dimensions
in a block vary jointly, while unlisted dimensions become singleton blocks.

The first proposal is the declared start sample. Coordinate strategies use the
best successful historical sample as their next anchor. Proposals are unique
and stop when `max_samples` or the finite space is exhausted.

PlaybookOptimizer always maximizes one finite numeric `score`. There is no
canonical objective metric or direction. Minimization belongs in the
SampleTrial score transformation, for example `score = -loss`.

## SampleTrial

`[playbooks.optimizer.sample_trial]` requires:

- `type`: an explicit component-local class reference in the exact form
  `@comp/path.py:ClassName`;
- `dataset`: the dataset path passed to the class;
- optional free-form `[playbooks.optimizer.sample_trial.params]`.

Built-in table detection is referenced as
`@comp/zemi/sample_trial.py:TableDetectionSampleTrial`. Short built-in names are
not canonical. The resolved class MUST inherit `SampleTrial`.

```python
dataset = sample_trial.load_dataset()
runs = sample_trial.run(playbook=playbook, sample=param_sample, dataset=dataset)
metrics, score, feedback = sample_trial.evaluate(runs=runs)
result = sample_trial.result(
    param_sample=param_sample,
    runs=runs,
    metrics=metrics,
    score=score,
    feedback=feedback,
)
```

`metrics` is the complete mapping of finite numeric diagnostics. `score` is one
finite number and alone controls ranking and the best sample. `feedback` is
optional JSON-compatible domain data. A SampleTrial may add domain Markdown via
`render_report`; ZEMI owns generic lifecycle and ranking output.

## Trial hierarchy and reports

Variable Playbooks execute:

`JobTrial → PlaybookTrial → SampleTrial → PlaybookRun`

Reports retain every parameter sample, run, complete metrics mapping, score,
feedback, failure, timestamp, artifact, ranking, best parameters, and optimizer
configuration. Rankings are descending by successful finite score.

## Migration from 0.3

Params 0.3 input is accepted only as a deprecated migration source. Supported
documents are normalized in memory to 0.5 with a `DeprecationWarning`. New and
edited files MUST:

- remove `param_space_mode`;
- rename `[playbooks.sampler]` to `[playbooks.optimizer]`;
- remove `objective`;
- replace `implementation` with explicit `type`;
- rename SampleTrial `path` to `dataset`;
- return `(metrics, score, feedback)` from custom `evaluate` methods.
