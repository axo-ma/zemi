# Dataset Report Specification

Status: accepted for implementation.
Date: 2026-09-24.

## Scope and Location

One Dataset Report per module with a TrialDataset, shared across all samples, in both `start_only` and `optimize` modes. Location: `<module_id>.dataset.md` in the job run directory.

For the worksheet dataset, one item represents one worksheet in one workbook. This report summarizes worksheet detection across samples. Do not include table-detection counters, table-detection rates, or per-table detail sections at any level of this dataset reporting layout, including item reports.

## Header

Show the dataset name, module filename, Job run ID, item count, and sample count. Provide relative links to Job Report and Module Report. Counts reflect the actual data available, not an assumed sample budget.

## Items

| Item ID | Worksheets detected<br>(OK / Total) | Worksheet detection rate |
|---|---:|---:|
| Workbook.xlsx::Sheet1 | 8 / 10 | 80% |

- One row per dataset item.
- Append a separate `Target` column with expected ranges as a JSON array.
- Append one prediction column per sample, in execution order. Its heading links to the Sample Report and includes the encoding format when available. `[]` means no tables; `—` means a prediction is unavailable. If multiple runs exist for the same item and sample, separate their predictions with semicolons.
- Limit prediction text to 60 Unicode characters before Markdown escaping. Append a clickable `...` linking to the corresponding Dataset Item Report when the full text is longer. The item report retains the full prediction. Do not truncate Target.
- Item ID uses meaningful workbook and worksheet names, with relative path disambiguation where necessary, rather than arbitrary labels such as validation-01. Dataset IDs use these names in experiment6.
- Item ID links to the source workbook; identify the worksheet explicitly. Do not promise direct worksheet navigation unless supported by the viewer.
- The entire Worksheets detected counter links to the item's separate Worksheet Detection Report in `dataset-items/`.
- Worksheet detection rate is plain text, calculated as OK / Total; show `—` when Total is zero.
- Aggregate worksheet evaluations across all samples. Worksheet success uses exact equality of validated range sets; repeated identical predicted ranges are deduplicated.
- Total includes every started worksheet check, including execution and evaluation failures. Such failures are not OK.
- Do not embed per-item detail sections in this summary report.

## Rendering

TrialDataset renders the summary MD fragment through its overridable `render_report` method, with DefaultReportRenderer support. ReportWriter persists it via `write_trial_dataset`.

See [Worksheet Detection Report](worksheet-detection-report.spec.md) and [set-based evaluation](table-detection-evaluation.spec.md).
