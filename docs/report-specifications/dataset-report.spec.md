# Dataset Report Specification

Location: `<module_id>.dataset.md` in the run root.

One row per Dataset Item, one prediction column per Sample. Columns:
`Item ID | Matches | Target | Sample 1 (...) | Sample 2 (...) | ...`.

The first column, Item ID, links directly to the source Excel file specified by
`input.workbook_path`. Resolve @comp/@inst paths and URL-encode the relative
file link. If no workbook is supplied or the file is missing, show plain Item ID.
The second column, Matches, links to the corresponding Dataset Item Report
under `dataset-items/`, including when its value is `—`.
Sample headers link to Sample Reports.
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
remain `[]`. A single range is plain text, with no link or disclosure control.
Multiple ranges display their first range followed by `...` in a collapsed
HTML `<details><summary>` cell. Expanding reveals the complete prediction
inside that cell; it never navigates to a different report. The same control
can be closed again. Short scalar results remain plain text; other long generic
results use a 60-character summary followed by `...` and disclose the full value.
The renderer does not assume a field named ranges.

Errors with details display `Error...` as a collapsed disclosure. Its contents
include execution/evaluation errors and the exact `prediction.raw_response`
when supplied, otherwise the returned prediction value. An error without
any details displays plain `Error`. All full error responses remain in Dataset
Item Reports too. Stored outputs remain unchanged.

Disclosure HTML stays on one physical Markdown table line. Escape HTML, pipes,
and line breaks in model responses to prevent markup injection or broken rows.
Rendering requires a Markdown preview that supports HTML details inside tables.
No JavaScript is required. Sample prediction cells no longer link to other reports;
the Matches column provides the Dataset Item Report navigation.
Target is never replaced by a checkmark. Report Sample naming is unchanged.

Standalone TrialDataset rendering uses this same renderer and layout.
