# Sample Report Specification

Location: `samples/<registered-sample-name>.md` inside the run.

Sections, in order:

1. Parameters: complete selected parameter values.
2. Evaluation: Score and Metrics. Feedback is retained in structured results
   but is not included in the Markdown report.
3. Prompt: the selected full named template, including examples and the item
   placeholder. Use the launch-time source snapshot. Omit only when no prompt
   binding is supplied. Expanded per-item input is not inserted here.
4. Runs: `Item ID | Run | Target | Prediction | Metrics | Error`.

Item ID and Run link to their registered reports. Target and prediction may
be arbitrary JSON-compatible values. The comparison_prediction contract and
exact-match rules are identical to Dataset Report. Long predictions link to
Run Reports; outputs are preserved there in full.

Metrics follow the shared slash format: names only in the header, values in
the same order in rows, `—` for missing values. The list is derived from data.
TableDetectionSampleTrial uses this common renderer, not a special table.

Top navigation contains exactly one link: **Back to Module Report**. No Job or individual Run links appear in the header. Links in the Runs table are retained.
