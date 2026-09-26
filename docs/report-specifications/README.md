# ZEMI Reporting Specifications

These specifications govern Markdown reports for one ZEMI job run. All generated report text is in English. Source filenames, worksheet names, identifiers, and data retain their original content.

- [Reporting architecture](reporting-architecture.spec.md)
- [ReportWriter and fragment registry](report-writer.spec.md)
- [Job Report](job-report.spec.md)
- [Module Report](module-report.spec.md)
- [Module Runs Report](module-runs-report.spec.md)
- [Dataset Report](dataset-report.spec.md)
- [Reproduction Report](reproduction-report.spec.md)
- [Dataset Item Report](dataset-item-report.spec.md)
- [Set-based table evaluation](table-detection-evaluation.spec.md)
- [Module execution pseudocode](module-execution.pseudocode.md)

## Resolved decisions

- One writer is shared for the entire job. ModuleOptimizer is the optimizer.
- The pseudocode deliberately omits reporting calls; actual calls belong to the existing lifecycle.
- `index.md` is the Job Report. Optimization progress is a fragment of the Module Report, not a separate file.
- Registered references determine safe filenames and relative links. A writer never links to a report that has not been created.
- Matches uses evaluator-confirmed exact_match across all started results. Errors do not match; unavailable equality is shown as `—`.
- Predicted and reference ranges use set semantics after range validation.
- `Samples` and `Runs` counters count actual started work and display `(OK / Total)` on a second header line.

- [Sample Report](sample-report.spec.md)
- Duration uses `2m 15s` or `1h 08m`; LM Time remains seconds.
