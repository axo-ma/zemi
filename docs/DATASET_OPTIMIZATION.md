# Dataset optimization

This document refines the experimental layer in
[ZEMI architecture](ZEMI_ARCHITECTURE.md).

`ZemiComponent.run()` resolves and validates one SampleTrial before creating an
Arsenal session. Each PlaybookOptimizer proposal runs the complete SampleTrial,
appends its metrics, score, and feedback to history, then proposes again.
Coordinate strategies recompute neighborhoods around the highest-score sample;
ties retain the earlier sample. `block_coordinate` varies declared blocks
jointly and treats unlisted dimensions as singleton blocks.

Variable Module ParamSpaces use `mode = "optimize"` for the full loop or
`mode = "start_only"` for one complete start-sample trial. Fixed Modules execute
once without a SampleTrial. See [Params 0.6](ZEMI_PARAMS_0.6.md).

## SampleTrial contract

SampleTrial is the public sample execution and evaluation extension point. A class is selected
as `@comp/path.py:ClassName`, MUST inherit `SampleTrial`, and is trusted Component
code confined to the Component path. One instance represents one parameter
sample. The framework constructs the generic `SampleTrialResult`; a custom
SampleTrial does not implement a `result()` method.

```python
param_space = ParamSpace(config=module.params)
optimizer = ModuleOptimizer(config=module.optimizer, param_space=param_space)

trial_dataset = TableDetectionTrialDataset(config=module.optimizer.trial_dataset)
trial_dataset.load()
history = []

while (param_sample := optimizer.next_param_sample(history)) is not None:
    sample_trial = TableDetectionSampleTrial(
        config=module.optimizer.sample_trial,
        module=module,
        param_sample=param_sample,
        dataset=trial_dataset,
    )
    runs = sample_trial.run()
    metrics, score, feedback = sample_trial.evaluate(runs)
    md_fragment = sample_trial.render_report(runs, metrics, score, feedback)
    history_item = SampleTrialResult(
        sample=param_sample, runs=runs, metrics=metrics,
        score=score, feedback=feedback,
    )
    history.append(history_item)

best = optimizer.best_param_sample(history)
```

Report writing is intentionally omitted from this conceptual loop. In the runner,
one job-scoped ReportWriter stores each Sample and Run Report, the Dataset Report,
and optimization progress within the Module Report. `history_item.report` holds
the registered Sample Report path. See [report specifications](report-specifications/README.md).

Score alone drives descending ranking. Metrics remain complete diagnostics.
Feedback is optional JSON-compatible domain data.

## DatasetItem and table dataset v1

A DatasetItem has an `id`, Module-facing `input`, and evaluator-facing
`ground_truth`. For example:

```json
{"items":[{"id":"sheet-1","input":{"workbook_path":"@comp/data/book.xlsx","worksheet_name":"Данные"},"ground_truth":["A1:B2"]}]}
```

The built-in `TableDetectionSampleTrial` uses one reviewed JSON file per split.
It validates workbook paths, worksheet names, range syntax, and duplicate
ground-truth entries before model startup. Items with empty ground truth are
explicit negative worksheets.

Predictions contain an array of uppercase inclusive A1 rectangles. Matching is
set intersection per worksheet after validation and deduplication. TP, FP, and FN are summed across the split;
precision, recall, and F1 derive from those totals. Invalid responses and model
errors receive the documented FP/FN penalty, remain visible in run records, and
do not truncate the remaining dataset. Aggregate and per-item metrics plus tag
diagnostics are preserved. TableDetectionSampleTrial returns aggregate F1 as
its maximized score.

## Reports and holdout evaluation

`job_trial.playbook_trials[].samples[].runs[]` retains stable ids, sample params,
timestamps, predictions, errors, score, full metrics, feedback, artifacts,
ranking, best sample, and best params in `report.json`. The job's Markdown entry
point is `index.md`; SampleTrial and TrialDataset can override their Markdown
fragments, which ReportWriter places in the registered reports.

For held-out evaluation:

```python
component = ZemiComponent.from_best_report(test_toml, validation_report)
```

The library validates reported parameters against the test configuration and
freezes optimization to one replay sample while preserving the configured test
dataset. Never optimize on test metrics.
