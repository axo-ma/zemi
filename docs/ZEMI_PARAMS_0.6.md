# ZEMI Params 0.6

Params 0.6 is the canonical declarative format for a ZEMI Component. Its
universal hierarchy is `System → Component → Module`. A Playbook is one Module
kind, not the universal third level.

```toml
[system]
version = "0.6"

[component]
name = "table-search"

[[arsenals]]
id = "local-llm"
config_path = "@comp/zemi/llm.toml"
lifecycle = "job"

[[modules]]
id = "detect-tables"
kind = "playbook"
path = "@comp/playbooks/detect_tables.ipynb"
arsenal = "local-llm" # optional

[modules.params]
worksheet_name = "Данные"
temperature = { values = [0.0, 0.2] }

[modules.optimizer]
mode = "optimize"
strategy = "grid"
max_trials = 30

[modules.optimizer.sample_trial]
type = "@comp/zemi/sample_trial.py:TableDetectionSampleTrial"

[modules.optimizer.trial_dataset]
path = "@comp/data/experiment5/validation.json"
```

Only `kind = "playbook"` has a runtime in 0.6. Other kinds are rejected clearly;
the Module contract permits adding them later without pretending they work now.

## Variable dimensions and optional start

Variable wrappers contain either `values` or `range`, plus an optional `start`.
When `start` is absent, `values` uses its first element and `range` uses `min`.
An explicit `start` takes priority and must belong to the generated domain.
`values` must be a non-empty array of unique finite JSON-compatible values.
`range` requires finite numeric `min <= max` and `step > 0`; its domain is
`min + n * step` up to `max`, inclusive when reached. A start between steps
is invalid even when it lies between `min` and `max`.

```toml
temperature = { values = [0.0, 0.2, 0.5] } # start = 0.0
temperature = { range = { min = 0.0, max = 1.0, step = 0.2 } } # start = 0.0
temperature = { values = [0.0, 0.2, 0.5], start = 0.2 } # explicit override
```

These are alternative definitions, not entries to combine in one TOML table.
Objects and arrays are indivisible choices: the complete first object (for
example, an `encoding_prompt` binding) becomes the start without duplication
in the configuration. ParamSpace copies values so samples remain independent.

The order of `values` determines the implicit start, including in `start_only`.
Grid visits the complete start sample first, then the Cartesian product in
parameter declaration and domain order, skipping the start already visited.
Omitting `start` produces the same traversal and optimizer behavior as explicitly
specifying that default. Random, coordinate, and block-coordinate strategies
also begin with that start and never propose an already observed sample.
Resolution and structural validation contracts are unchanged.

## Structural and resolution rules

Paired prompt/encoder parameter values use the existing `values`/`start`
mechanism. See [Encoding and prompt packages](ENCODING_PROMPTS.md) for the
package layout, named Markdown templates and deterministic Sample names.

An explicit component parameter TOML may be located anywhere inside the
component root and selected with `@comp/path/to/file.toml`. Bare filenames and
automatic selection continue to use the root `params/` directory.

The only top-level sections are `system`, `component`, `arsenals`, and `modules`.
Structural tables are closed. Free-form Module inputs belong only in
`[modules.params]`. Arsenal definitions are peers of Modules, and both Arsenal
definitions and a Module's `arsenal` reference are optional.

`ref`, `__include__`, `select`, and `input` resolve before ParamSpace is built.
References may target only a `params` section. ParamSpace is constructed solely
from the resolved `modules.params`; optimizer and SampleTrial configuration can
never become dimensions.

`mode` may be a resolved value or use the existing selection mechanism:

```toml
[modules.optimizer]
mode = { select = ["optimize", "start_only"] }
```

- Concrete Module params without an optimizer execute the Module once.
- `start_only` executes one complete SampleTrial at the explicit or default start sample,
  including dataset runs, evaluation, history, best sample, and report.
- `optimize` runs the full score-maximizing loop.

### Kernel reuse

`[modules.optimizer].reuse_kernel` is a boolean, default `true`, in both
`optimize` and `start_only`. One Python kernel executes all dataset Runs of
all samples in that Module. Set `reuse_kernel = false` for a new kernel per
Run. The flag is optimizer configuration, never a Module input or dimension.
Modules without an optimizer continue to use an independent kernel.

Before each shared-kernel Run, ZEMI resets the interactive variable namespace,
restores the launch environment/Python path and component working directory,
and resets output publication. Imported packages and managed client caches
remain loaded. Mutable globals inside imported user modules are not reset;
stateful playbooks requiring full process isolation should disable reuse.
Failures invalidate the kernel; subsequent Runs start a new one. ZEMI closes
the kernel after the Module, including error paths. Arsenal lifecycle and
model activation are unchanged. OpenAI clients with the same connection/model
configuration are reused inside that kernel; changed configurations create
separate clients. Requests do not accumulate conversation history.

Papermill remains the executor. Each Run retains its own output IPYNB and
cell timings. Successful execution consumes the returned notebook in memory;
failed execution may read the partial notebook. Automatic HTML export is removed.

`[modules.optimizer.trial_dataset]` owns the dataset path. Its `path` field is
required for variable Module parameters.
`TableDetectionTrialDataset(config=trial_dataset).load()` validates the flat
dataset once before Arsenal starts. The SampleTrial section selects the class
and holds only its optional `params`. Each parameter sample gets a SampleTrial
instance constructed with the Module, ParamSample, and dataset.
`SampleTrial.run()` executes once per DatasetItem.
Only `DatasetItem.input` and Module params cross the Module boundary.
`SampleTrial.evaluate(runs)` sees evaluator-only `ground_truth`, optional
`description`, and optional `tags`. It returns `(metrics, score, feedback)`. All finite numeric metrics
are retained, while the optimizer always maximizes the single finite `score`.
The runner constructs a `SampleTrialResult` for history from params, runs,
metrics, score, feedback, errors, artifacts, and the individual Sample Trial
Report path. SampleTrial renders only its own trial; ModuleOptimizer renders
Optimization Progress and TableDetectionTrialDataset renders the cross-trial
item report. Optimization Progress links to the saved dataset report.

## Dataset item

```json
{"items":[{"id":"sheet-1","description":"Основная таблица","input":{"workbook_path":"@comp/data/book.xlsx","worksheet_name":"Данные"},"ground_truth":["A1:D20"],"tags":["header"]}]}
```

Only `input` is passed to a Module. `ground_truth`, `description`, and `tags` remain evaluator-side.
User-facing JSON, notebook parameters, snapshots, logs, and reports are UTF-8
and preserve readable Unicode rather than emitting literal `\\uXXXX` escapes.

## Migration

Params 0.5 documents are accepted as a deprecated migration source. They are
normalized in memory to 0.6 with a `DeprecationWarning`: `[[playbooks]]` becomes
`[[modules]]` and gains `kind = "playbook"`; `sample_trial.dataset` moves to
`trial_dataset.path`. New and edited files must use 0.6.
The older 0.3 migration path remains available and also produces canonical 0.6.
