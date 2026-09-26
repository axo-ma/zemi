# Dataset Item Report Specification

Location: `dataset-items/<registered-item-name>.md` inside the run.

Title: Dataset Item, followed by Item ID and Job run ID. Input is a Parameter /
Value table built from all item.input fields. Target is item.ground_truth.

Results columns: `Sample | Run | Prediction | Metrics | Error`.
Sample and Run link to their reports. Prediction uses the generic comparison
value, with ✅ for confirmed exact matches. Full mismatches are displayed here
without truncation, so Dataset summary links expose the complete result.

Metric names are collected from all result metrics, sorted once, and placed
after `Metrics<br>` in the header separated by ` / `. Each row lists values in
that order, with `—` for missing metrics. Numeric values use three decimals.
No metric names, input fields or prediction keys are prescribed by the task.

Standalone TrialDataset rendering uses this same layout. There are no
Worksheet Detection, Expected ranges or task-specific summary sections.
