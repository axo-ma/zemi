# Model response errors in table detection

A completed model request with invalid output is not an execution failure.
Playbooks should publish the original `raw_response` and a concise
`response_error` string in their output mapping instead of raising a parsing
exception. Preserve normal exceptions for transport, runtime and program errors.

The table evaluator penalizes `response_error` with the existing error scoring
rules; it does not convert the response into a successful prediction.
The module report distinguishes **Model response error** from **Execution
failure** and displays the escaped raw response in an expandable cell.
Technical execution details remain available separately.

Sample columns in module reports must remain compact. The visible error
label is a short red **Error**; only the full original model response belongs inside expandable details.
Classification and reason remain in report data. Follow the canonical
[Module Report Layout](report-specifications/module-report-layout.spec.md)
for sizing and click behavior.
