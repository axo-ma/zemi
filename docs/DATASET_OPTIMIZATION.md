# Dataset optimization

`ZemiComponent.run()` validates every enabled playbook's complete dataset and
resolves its adapters before creating an Arsenal session. Each sampler proposal
runs the playbook for every item, then evaluates the completed sample, observes
the result, and proposes again. Coordinate strategies recompute neighborhoods
around the best observed sample; ties retain the earlier sample. Search stops
when that neighborhood has no unseen candidates or the sample limit is reached.

See [Params 0.3](ZEMI_PARAMS_0.3.md) for the normative structure and resolution.

## Adapter contracts

Built-in datasets: `table_detection`, `jsonl`, `csv`. Built-in evaluator:
`table_detection`. Default run adapter: `notebook`.

A local adapter is `@comp/path.py:function`. Paths must resolve inside the
component, including symlink resolution. Imports by arbitrary module name,
absolute paths, external entry points and arbitrary expressions are rejected.
Local Python adapters are trusted component code, not sandboxed plugins: their
module-level code executes at resolution. Do not load untrusted implementations.

Callable signatures:

```python
def load(*, path, params):
    return [{"id": "item-id", "input": {...}, "ground_truth": ..., "tags": [...]}]

def run(sample, input, *, execute, context, params):
    # No ground truth or completed run records are passed here.
    # context.workbook(input["workbook_path"]) caches one open book per sample.
    return execute({"dataset_input": input})  # executes notebook with sample params

def evaluate(completed_sample_trial, *, params):
    return {"f1": 0.5}, {"diagnostics": ...}
```

`execute` returns the notebook's `zemi.playbook.output_params` mapping. The
default notebook adapter injects only `dataset_input`. A custom run adapter may
prepare an input representation using `context.workbook` and inject it instead.
The context closes before evaluation and on failure. `table_detection` items
are grouped by workbook in source workbook order. No open Excel objects are
retained in dataset items. The default notebook adapter leaves workbook opening
to the notebook; to reuse a workbook between sheets use an input preparation
adapter and `RunContext` as Experiment 5 does. Adapters must not independently
open additional books while retaining the context's current book.

JSONL/CSV are transport loaders: records still need an `input` field. A local
loader can convert application-specific records. Dataset and adapter params are
host-side configuration and are never implicitly inherited by notebook params.
This is data-flow separation, not an OS filesystem sandbox for trusted notebooks.

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
metrics/feedback, ranking and best sample. `main.md` and `report.md` render the
same dataset results. Runtime dataset inputs can contain absolute resolved paths;
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
