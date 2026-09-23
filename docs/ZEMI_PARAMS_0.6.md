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
temperature = { values = [0.0, 0.2], start = 0.0 }

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

## Structural and resolution rules

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
- `start_only` executes one complete SampleTrial at the declared start sample,
  including dataset runs, evaluation, history, best sample, and report.
- `optimize` runs the full score-maximizing loop.

`[modules.optimizer.trial_dataset]` owns the dataset path. Its `path` field is
required for variable Module parameters. `TrialDataset.load(path)` validates the
flat dataset once before Arsenal starts. The SampleTrial section selects the
class and holds only its optional `params`.
`SampleTrial.run(module, param_sample, dataset)` executes once per DatasetItem.
Only `DatasetItem.input` and Module params cross the Module boundary.
`SampleTrial.evaluate(runs, dataset)` sees evaluator-only `ground_truth`, optional
`description`, and optional `tags`. It returns `(metrics, score, feedback)`. All finite numeric metrics
are retained, while the optimizer always maximizes the single finite `score`.
History stores params, runs, per-item metrics, score, optional feedback, errors,
artifacts, and the individual Sample Trial Report path. SampleTrial renders only
its own trial; ModuleOptimizer renders Optimization Progress and TrialDataset
renders the cross-trial item report.

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
