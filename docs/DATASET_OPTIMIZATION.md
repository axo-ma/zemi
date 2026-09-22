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

SampleTrial is the only public experiment extension point. A class is selected
as `@comp/path.py:ClassName`, MUST inherit `SampleTrial`, and is trusted Component
code confined to the Component path.

```python
sample_trial = TableDetectionSampleTrial(config=module.optimizer.sample_trial)
trial_dataset = TrialDataset.load(sample_trial.config.dataset)

while param_sample := optimizer.next_param_sample(history):
    runs = sample_trial.run(module=module, param_sample=param_sample, dataset=trial_dataset)
    metrics, score, feedback = sample_trial.evaluate(runs=runs, dataset=trial_dataset)
    report = sample_trial.render_report(param_sample=param_sample, runs=runs, metrics=metrics, score=score, feedback=feedback)
    history.append(sample_trial.result(
        param_sample=param_sample,
        runs=runs,
        metrics=metrics,
        score=score,
        feedback=feedback, report=report,
    ))

best = optimizer.best_param_sample(history)
optimizer.render_report(history=history, best_param_sample=best)
trial_dataset.render_report(history=history)
```

Score alone drives descending ranking. Metrics remain complete diagnostics.
Feedback is optional JSON-compatible domain data.

## DatasetItem and table dataset v1

A DatasetItem has an `id`, Module-facing `input`, and evaluator-facing
`reference`. For example:

```json
{"items":[{"id":"sheet-1","input":{"workbook_path":"@comp/data/book.xlsx","worksheet_name":"Данные"},"ground_truth":["A1:B2"]}]}
```

The built-in `TableDetectionSampleTrial` uses one reviewed JSON file per split.
It verifies policy and workbook files, SHA-256 hashes, worksheet links and
statuses, range syntax, and duplicate annotations before model startup. Listed
reviewed worksheets without annotations are explicit negative items.

Predictions contain an array of uppercase inclusive A1 rectangles. Matching is
multiset intersection per worksheet. TP, FP, and FN are summed across the split;
precision, recall, and F1 derive from those totals. Invalid responses and model
errors receive the documented FP/FN penalty, remain visible in run records, and
do not truncate the remaining dataset. Aggregate and per-item metrics plus tag
diagnostics are preserved. TableDetectionSampleTrial returns aggregate F1 as
its maximized score.

## Reports and holdout evaluation

`job_trial.playbook_trials[].samples[].runs[]` retains stable ids, sample params,
timestamps, predictions, errors, score, full metrics, feedback, artifacts,
ranking, best sample, and best params. Generic Markdown is produced by ZEMI;
SampleTrial may append domain-specific sections.

For held-out evaluation:

```python
component = ZemiComponent.from_best_report(test_toml, validation_report)
```

The library validates reported parameters against the test configuration and
freezes optimization to one replay sample while preserving the configured test
dataset. Never optimize on test metrics.
