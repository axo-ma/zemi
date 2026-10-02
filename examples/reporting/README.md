# Module report exports

Reporting works automatically for ordinary modules and optimizer modules.
No report configuration needs to be added to the parameter TOML.

For a module named `detect-tables`, open these files inside the job run:

| File | Purpose |
|---|---|
| `detect-tables.cmd` | Local viewer; captured one-call model contexts can open terminal chat |
| `detect-tables.html` | Standalone report, same palette, offline expandable cells |
| `detect-tables.inline.html` | Ready scoped fragment for embedding in Codex |
| `detect-tables.md` | Markdown for GitHub and archives |
| `detect-tables.json` | Full module data for analysis; numbers retain precision |
| `detect-tables.runs.md` | Run summary with identifiers |
| `detect-tables.reproduction.md` | Launch reproduction instructions |

The optimized report starts with Samples, then Items, then metadata and
configuration. Each sample header in Items includes its score on a second line.
All samples tied for the greatest score are green. The dataset remains generic:
targets and predictions do not need to be Excel ranges, and metrics are derived
from the evaluator's returned mapping.

Module JSON contains `schema_version`, `job_run_id`, `module_id`, `status`,
`configuration`, `reproduction`, `samples`, `items`, `execution` and `sections`.
Samples include `number`, `id`, `params`, `score`, `metrics`, `status`, `duration`
and `runs`. Items include their original fields and one-based `number`.
Runs retain predictions, errors, evaluation metrics, captured chat contexts and
links to retained artifacts. Rendered `sections` preserve custom client content.

One successful output notebook is kept per sample, plus every failed output
notebook. This retention policy does not alter notebook execution or extraction
of published outputs. No separate Dataset Report or per-run report is created.

To export an existing standalone report as an inline fragment:

```python
from zemi.report_viewer import write_inline_report

report = component.run_directory / "detect-tables.html"
write_inline_report(report, report.with_suffix(".inline.html"))
```

The inline export contains no terminal-launch bridge or local file links.
Native details controls continue to work. See the normative
[report writer specification](../../docs/report-specifications/report-writer.spec.md)
and [viewer documentation](../../docs/REPORT_VIEWER.md).
