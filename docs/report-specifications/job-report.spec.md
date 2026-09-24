# Job Report Specification — index.md

Status: accepted for implementation.
Date: 2026-09-23.

This file specifies the entry page for a job run.

## 1. Purpose

The entry report contains only run metadata and a module summary. Each module name is a relative hyperlink to a separate Markdown report for that module. Detailed parameters, sample and run results, metrics, errors, and artifacts belong in the module report.

The entry report filename is `index.md`.

## 2. Run Metadata

The metadata layout is identical across all execution modes.

Example with illustrative data:

> **Job:** `compare_parameters.py` · **Component:** `document_processing`<br>
> **Job run ID:** `run260923-143000` · **Status:** Failed<br>
> **Started:** 2026-09-23 14:30:00 +03:00 · **Duration:** 12 min 34 s

- Start directly with Job and Component. Do not add an experiment title or an experiment field: a job run is not necessarily an experiment.
- Include the job, component, job run identifier, status, start time with timezone, and duration.
- Job must display the actual filename of the launched job entry point, including its extension, rather than a separate display name or alias. `compare_parameters.py` is an illustrative filename; use the actual launched filename. This filename is distinct from Job run ID, which identifies a particular execution and its output directory.
- Job run ID is the name of the job run directory containing this run's reports and results. Read it from the actual run directory name; do not generate a separate identifier for the report. It is not a module run ID, a sample ID, or a separate report ID.
- Concrete example: `run260923-143000` identifies a run started on September 23, 2026 at 14:30:00. Its directory is `<component_root>/.tmp/run260923-143000/`, and the entry report path is `<component_root>/.tmp/run260923-143000/index.md`.
- The default directory naming format is `runYYMMDD-HHMMSS`. If that name already exists, a numeric suffix is added, for example `run260923-143000-01`. Preserve the complete directory name, including any suffix, in Job run ID. The identifier does not encode a timezone; Started displays the timezone separately.
- Implementation reference: `zemi/env.py`, the `runid` property, provides the run directory; `zemi/component.py` uses the directory name as `job_trial_id`.
- While execution is in progress, duration is measured up to the report update time.
- Status describes job execution, not evaluation quality.

## 3. Module Summary Without an Optimizer

Each module runs once with the specified parameters. Sample, best result, and score columns are omitted.

| Module | Status | Duration | Output |
|---|---|---|---|
| [toybook](toybook.md) | Succeeded | 52 s | [HTML](notebooks/toybook-output.html) · [IPYNB](notebooks/toybook-output.ipynb) |
| [validation](validation.md) | Failed | 23 s | [IPYNB](notebooks/validation-output.ipynb) |
| [export](export.md) | Not started | — | — |

Example links illustrate the report structure; they do not point to existing specification files.

### Direct Notebook Output Links

- For a notebook module, Output provides direct links to the executed output notebook in HTML and IPYNB formats, when available. The user can open either artifact directly from the summary without first opening the module report.
- These links target generated output artifacts, not the source notebook.
- The module name continues to link to the module report; Output links are additional navigation.
- Show only artifacts that were actually produced, including partial output from a failed execution. If neither format is available, display `—`.
- Resolve links from the actual artifact paths relative to `index.md`. The filenames in the example are illustrative and do not prescribe output artifact naming.
- For modules without notebook output, display `—`; other output types are outside the scope of this rule.

## 4. Module Summary With a Configured Optimizer

The `start_only` and `optimize` modes share the same table structure, with a mandatory Mode column.

| Module | Mode | Status | Samples<br>(OK / Total) | Runs<br>(OK / Total) | Result | Score | Duration |
|---|---|---|---:|---:|---|---:|---|
| [extraction](extraction.md) | `optimize` | Succeeded | [8 / 8](extraction.md#samples) | [160 / 160](extraction.runs.md) | [sample-006](samples/extraction.sample-sample-006.md) | 0.94 | 7 min 10 s |
| [validation](validation.md) | `start_only` | Succeeded | [1 / 1](validation.md#samples) | [20 / 20](validation.runs.md) | [sample-001](samples/validation.sample-sample-001.md) | 0.87 | 44 s |

### Summary Links and Column Labels

- Render Samples and Runs with `(OK / Total)` on a second line in the same header cell, using `Samples<br>(OK / Total)` and `Runs<br>(OK / Total)`.
- The complete Samples counter is a link to `<module_id>.md#samples`, the module's sample table.
- The complete Runs counter is a link to `<module_id>.runs.md`, the separate Module Runs Report listing individual runs across all samples.
- Result links to the existing Sample Report of the selected sample under `samples/`. There is no separate selected-sample report type.
- In `optimize`, Result identifies the optimizer-selected sample; in `start_only`, it identifies the starting sample. If no result report exists, show `—` rather than a broken link.
- Module names continue to link to root-level Module Reports. HTML/IPYNB links in the single-execution table continue to open generated notebook artifacts directly.
- Resolve Sample Report filenames from registered report references, not by reconstructing them in the renderer. Example filenames are illustrative; the writer encodes identifiers safely and resolves collisions.

### optimize Mode

- Result identifies the best sample selected by the optimizer.
- While execution is in progress or after interruption, it is the best among the samples already evaluated.
- Score belongs to the selected sample.

### start_only Mode

- Only the starting sample is executed, without searching for additional parameter sets.
- Result identifies the starting sample; it is not described as the best sample.
- Score is displayed once evaluation is available.

A configured optimizer does not imply that a search took place: the actual execution mode is always shown in the table.

## 5. Mixed Execution

For a job containing modules both with and without an optimizer, show two groups within Module Summary:

1. Single Execution — the table defined in section 3.
2. Execution With Optimizer — the table defined in section 4, covering both `start_only` and `optimize`.

Empty groups are omitted. Within each group, modules retain their order from the job. Mixed execution uses this presentation.

## 6. Display Rules

| Element | Rule |
|---|---|
| Row | One module within the job run |
| Module name | Always links to a separate Markdown report, including failed and unstarted modules |
| Row order | Module order from the job within each group; no sorting by score |
| Module statuses | Not started / Running / Succeeded / Failed / Skipped / Interrupted |
| OK / total | Successfully completed / started, including running and failed executions |
| Unstarted samples and runs | Excluded from total; counters represent actual execution, not the plan |
| Missing result or score | Display `—`; a zero score is displayed as `0` |
| Score | Evaluation of the sample identified by Result; scores across different modules are not assumed comparable |
| Module duration | Total module execution time, including optimization and evaluation; for a running module, measured up to the report update time |
| Errors | Only status is shown on the entry page; details belong in the module report |

The status vocabulary and counter semantics are the reporting contract.

## 7. Proposed Report Paths

- `<run_directory>/index.md` — entry report.
- `<run_directory>/<module_id>.md` — module report.
- Module reports reside directly in the run directory alongside `index.md`, without a `modules` subdirectory. Users must be able to locate and open a module report directly from the run directory without navigating through the entry report.
- All links between reports are relative so that the entire run directory can be moved together.
- A module report must exist even for an unstarted, skipped, or failed module; its detailed structure will be defined in a separate specification.

## 8. Scope

This document does not define the internal structure of module, sample, run, dataset, or optimization reports. Each agreed report type will receive its own specification file.
