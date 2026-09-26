# Dataset Report Specification

Location: `<module_id>.dataset.md` in the run root.

One row per Dataset Item, one prediction column per Sample. Columns:
`Item ID | Matches | Target | Sample 1 (...) | Sample 2 (...) | ...`.

Item ID links to `dataset-items/`; Sample headers link to Sample Reports.
Matches shows evaluator-confirmed exact matches / all started results when
the evaluator provides `metrics.exact_match`; otherwise it is `—`.
Execution or evaluation errors cannot count as matches.

Targets and predictions may be arbitrary JSON-compatible values. The generic
renderer never extracts a task-specific key such as `ranges`. A SampleTrial
may publish `run.comparison_prediction` to identify its comparison result;
otherwise the complete `run.prediction` is displayed. The table-detection
adapter publishes its ranges as comparison_prediction, without removing any
outputs from the saved prediction.

Confirmed exact matches display ✅. Missing results display `—`; empty arrays
remain `[]`. Errors display an Error link. Values over 60 rendered characters
are shortened with a `...` link to the complete Dataset Item Report.
Target is never replaced by a checkmark. Report Sample naming is unchanged.

Standalone TrialDataset rendering uses this same renderer and layout.
