# Dataset optimization

This document refines the experimental layer defined by
[ZEMI architecture](ZEMI_ARCHITECTURE.md).

`ZemiComponent.run()` resolves one complete SampleTrial and validates its
dataset before creating an Arsenal session. Each sampler proposal runs the
SampleTrial, evaluates its runs, derives the configured score, appends the full
result to history, and proposes again. Coordinate strategies recompute neighborhoods
around the best observed sample; ties retain the earlier sample. Search stops
when that neighborhood has no unseen candidates or the sample limit is reached.
`block_coordinate` uses explicit named blocks: it explores each block's
Cartesian product around the current best sample, holds all other dimensions at
that best sample, and treats every unlisted dimension as a singleton block.

Dataset optimization runs only when the playbook explicitly sets
`param_space_mode = "sampler"` and defines `[playbooks.sampler]`. Use
`param_space_mode = "start_only"` to execute exactly the declared start/fixed
values once as an ordinary PlaybookRun without loading a dataset
and without creating a SampleTrial. Both modes require a sampler configuration;
only sampler mode requires `sample_trial`. A variable ParamSpace without an
explicit mode or sampler is rejected. Fixed-only Playbooks create no ParamSpace.

See [Params 0.3](ZEMI_PARAMS_0.3.md) for the normative structure and resolution.

## SampleTrial contract

SampleTrial is the only public extension point. The built-in implementation is
`table_detection`; a custom implementation is trusted Component code selected
as `@comp/path.py:ClassOrFactory`. Paths are confined to the Component. Arbitrary
imports, absolute paths, entry points, and expressions are rejected.

```python
sample_trial = SampleTrial(config)
dataset = sample_trial.load_dataset()

while sample := sampler.next_sample(history):
    runs = sample_trial.run(playbook=playbook, sample=sample, dataset=dataset)
    metrics, feedback = sample_trial.evaluate(runs=runs)
    score = metrics[sampler.objective.metric]
    history.append(sample_trial.result(
        sample=sample, runs=runs, score=score,
        metrics=metrics, feedback=feedback,
    ))

best_sample = sampler.best_sample(history)
```

The stable methods are `load_dataset`, `run`, `evaluate`, `result`, and
`render_report`. Standard `run` invokes the Playbook once for every DatasetItem.
Workbook caches and other special execution context belong inside the table
SampleTrial. `evaluate` sees runs and SampleTrial configuration only. It returns
the complete metrics mapping plus optional JSON feedback; objective selection is
outside it. Custom code is trusted, not sandboxed.

## Table dataset v1

One JSON per split:

```json
{
  "info": {"version": "1.0", "split": "validation"},
  "annotation_policy": "annotation_policy.md",
  "workbooks": [{"id": "book", "path": "relative/book.xlsx", "sha256": "<64 lowercase hex digits>"}],
  "worksheets": [{"id": "sheet", "workbook_id": "book", "name": "Data", "status": "reviewed", "tags": ["single_header"]}],
  "annotations": [{"id": "table", "workbook_id": "book", "worksheet_id": "sheet", "range": "A1:C5", "status": "reviewed"}]
}
```

Paths in this JSON are relative to the JSON file; `..` is allowed for sibling
datasets but must remain inside the ZEMI Instance after resolution. TOML paths
use `@comp/` or `@inst/`. Policy file and all books must exist. SHA-256 is checked
before reading each book. Sheet names, ids, links, reviewed statuses, range
syntax/bounds and duplicate annotations are checked before any model startup.
Selected worksheets are the explicit dataset membership; other worksheets in a
book are outside that split. All listed sheets must be reviewed. A listed
reviewed sheet without annotations is a negative item, never an omitted item.
Openpyxl-compatible workbooks are supported; legacy XLS/XLSB are not.

## Metrics and failures

Predictions must contain a `ranges` array of uppercase inclusive A1 rectangles.
Matching uses a multiset intersection within each item (workbook, worksheet).
Duplicate predictions beyond one matched occurrence count as FP. TP, FP, FN
are summed across the split; precision, recall and F1 derive from these totals.
No per-item F1 averaging is used. Header presence is determined by the separate
annotation policy, not by evaluator heuristics.

An exception or invalid response contributes one penalty FP and FN for every
reference table on that item. No partial recovery from malformed output is
scored. This explicit failure policy also penalizes errors on negative items.
`errors`, `items`, `reviewed_empty`, `correct_empty`, per-item diagnostics and
tag aggregates are included. Precision/recall with zero denominators are 0;
an entirely correct all-negative split has F1 = 1.

Model errors do not abort the current sample: all items are collected. A sample
with model failures can have a valid, penalized objective and remains eligible
for ranking. Its run records show failures; final job status is failed if a
notebook failed. Evaluator exceptions, missing/non-finite/boolean metrics fail
the SampleTrial, are recorded, and are excluded from objective comparisons.
`stop_on_error` applies to the enclosing playbook/job, not to truncating a dataset.

## Reports and holdout evaluation

The old `trials`, `playbooks`, and notebook artifact fields remain available.
`job_trial.playbook_trials[].samples[].runs[]` adds stable hierarchy ids,
sample ordinals and params, timestamps, predictions, references, errors,
scalar score, the full metrics mapping, feedback, ranking and best sample.
`report.json` remains the generic replay source. ZEMI renders the generic shell
in `main.md`, `report.md`, and a detailed Playbook SampleTrial report; each
SampleTrial appends domain Markdown from `render_report`. Results are saved
before domain rendering after every sample. Runtime dataset inputs can contain absolute resolved paths;
this does not change the rule for paths in source TOML.

For held-out evaluation, configure the test dataset and instantiate the
component from a completed validation report:

```python
component = ZemiComponent.from_best_report(test_toml, validation_report)
```

The library resolves every successful playbook's `best_sample`, then validates
the reported parameters against the test configuration. Fixed values must be
unchanged and variable values must belong to their declared domains. Sampling
is frozen to one replay sample. `sample_overrides` remains the lower-level API
for callers that already have a validated parameter mapping. Never optimize on
test metrics.
