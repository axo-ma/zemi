# Worksheet Detection Report Specification

Status: accepted for implementation.
Date: 2026-09-24.

## Identity and Location

One report per dataset item per module, aggregating that item's worksheet evaluations across samples. Proposed location: `dataset-items/<module_id>.<item_file_key>.md` under the job run directory.

Item file keys must be filesystem-safe and unique within the module. Preserve the complete meaningful Item ID in the report. Exact filename encoding remains an implementation design item; clients use registered report references instead of guessing paths.

## Header

Identify Item ID, workbook, worksheet, module filename, and Job run ID. Include the expected range set as the worksheet's reference data, not as a separate per-table report.

Provide relative links to the source workbook, `../<module_id>.dataset.md`, `../<module_id>.md`, and `../index.md`.

## Worksheet Detection Summary

Show Worksheets detected (OK / Total) and Worksheet detection rate for this item across samples. Total includes every started worksheet check, including execution and evaluation failures; such failures are not OK. Duplicate predicted ranges do not reduce success.

## Worksheet Detections

| Sample | Run | Detected ranges | Exact match |
|---|---|---|---|
| 1 | Run report | A6:C13 | Yes |

List the item's runs across samples in execution order. Sample numbers link to Sample Reports, and Run values link to individual Run Reports. Display execution or evaluation errors explicitly rather than treating an unavailable prediction as a valid empty set.

Do not include table-detection counters, table-detection rates, or per-table detail sections. This report evaluates the worksheet as a whole.

## Rendering

TrialDataset supplies an overridable `render_worksheet_detection_report` method. Its base implementation delegates to `DefaultReportRenderer.render_worksheet_detection_report` and returns an MD fragment.

The dedicated writer method is `write_worksheet_detection_report(module_id, item_id, md_fragment)`. It writes the item's report and returns its reference. This contract does not prescribe when execution code invokes report writing.
