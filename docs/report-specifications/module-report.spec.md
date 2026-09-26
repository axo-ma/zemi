# Module Report Specification

Status: accepted for implementation.
Updated: 2026-09-24.

This specification defines two layouts for the same report type: a module report without an optimizer and a module report with a configured optimizer. The latter covers both `start_only` and `optimize`.

## 1. Shared Rules

- Location: `<run_directory>/<module_id>.md`, directly beside `index.md`, without a modules subdirectory.
- Include a `Back to job report` link targeting `index.md`.
- The report must be understandable when opened directly from the run directory.
- Identify the module, its source filename, the launched job filename including extension, the component, and Job run ID.
- Job run ID is the actual run directory name, for example `run260923-143000`; it is not a module execution or sample identifier.
- Show module execution status, start time with timezone, and duration. While running, duration is measured up to the report update time.
- Status vocabulary and counter semantics follow [the job report specification](job-report.spec.md).
- Within each layout, retain the same section order for successful, running, failed, skipped, interrupted, and unstarted modules. Explain unavailable data instead of dropping sections.
- Use relative links to actual generated artifacts. Do not show links to artifacts that do not exist.
- Output notebook links target executed notebooks, not source notebooks. Partial outputs may remain accessible after failure.
- Display missing numeric results as `—`, never as zero. Do not infer evaluation quality from execution status.
- Mask secret parameter values. Preserve parameter names and show actual resolved values for an executed module; do not present configured values as resolved when resolution has not occurred.
- Examples below are illustrative. Artifact filenames, parameter names, and metrics are not prescribed by these examples.

## 2. Layout Without an Optimizer

The module executes once with the specified parameters. Do not introduce synthetic samples, optimization scores, or rankings.

### Header

Display a `Module: <module_filename>` heading, for example `Module: table_detection.ipynb`, and the backlink, followed by the fields below. Do not use a separate Source field. If needed to distinguish the configured module identity from its filename, display Module ID separately.

| Field | Content |
|---|---|
| Job | Launched job filename, for example `process_documents.py` |
| Component | Component name |
| Job run ID | Actual run directory name, for example `run260923-143000` |
| Execution | Single execution |
| Status | Module execution status |
| Started | Module start time with timezone, or `—` |
| Duration | Module duration, or `—` |

### Section 1: Parameters

Show a Parameter / Value table containing the effective input parameters of the single execution. Present long or structured values in readable blocks outside the compact table when necessary.

### Section 2: Results

Show the module's published output values in an Output / Value table. These are actual module outputs, not automatically generated conclusions. Keep structured output readable; link large artifacts in Module Artifacts.

When no outputs are published, state `No published output.` For incomplete execution, distinguish available partial results from a complete result.

### Section 3: Module Artifacts

Show an Artifact / Format / File table. For notebook modules, provide a direct IPYNB link when available. Automatic notebook HTML export is removed. Include other declared generated artifacts when available. If no files were produced, state `No output files available.`

### Section 4: Errors

Show execution errors and their effect on completion or output availability. Link available detailed diagnostics. If no errors have been recorded, state `No errors recorded.` This statement does not imply that an unfinished execution succeeded.

## 3. Layout With a Configured Optimizer

### Header

Use the same identity and timing fields as the layout without an optimizer. Replace Execution with Mode, displaying the actual `optimize` or `start_only` value.

### Section 1: Configuration

Show:

- Dataset identity and size, with a dataset report link when available.
- Optimizer identity and relevant configured settings, including the execution budget or stopping criteria when supplied.
- Evaluation method, score meaning, and optimization direction when defined by the configuration or evaluation contract. Do not invent a direction or score interpretation.
- A Parameter / Role / Value or search space table separating fixed parameters from parameters eligible for optimization.
- For `start_only`, explicitly state that the configured search space was not explored.

### Section 2: Module Execution Summary

Do not include a Runs table or individual run listings in this report. The Runs counter links to the separate Module Runs Report at `<module_id>.runs.md`, which lists runs across all samples and links to individual Run Reports.

Show Samples OK / total, Runs OK / total, and the actual stop reason. Totals count started executions, as in the entry report; configured budgets remain separately labelled in Configuration. If execution is still running, state that instead of inventing a stop reason.

Use two-line headers `Samples<br>(OK / Total)` and `Runs<br>(OK / Total)`. The Samples counter links to `#samples`; the Runs counter links to `<module_id>.runs.md`.

### Section 3: Samples

Expose stable anchor `samples` for links from the Samples counter in Job Report. Keep this anchor and an explicit empty state even before the first sample starts.

One row per started sample, in execution order:

| Sample | Parameters<br>temperature / top_p / top_k | Score ↑ | Status | Metrics<br>precision / recall / f1 | Runs<br>(OK / Total) | Duration |
|---|---|---:|---|---|---:|---|
| [1](samples/table_detection-sample-001.md) | 0.0 / 0.9 / 40 | 0.89 | Succeeded | 0.91 / 0.87 / 0.89 | [20 / 20](table_detection.runs.md#sample-001) | 52 s |
| [2](samples/table_detection-sample-002.md) | 0.3 / 0.95 / 50 | 0.94 | Succeeded | 0.96 / 0.92 / 0.94 | [20 / 20](table_detection.runs.md#sample-002) | 50 s |
| [3](samples/table_detection-sample-003.md) | 0.6 / 1.0 / 60 | — | Failed | — / — / — | [7 / 8](table_detection.runs.md#sample-003) | 23 s |

Column order is fixed: Sample, Parameters, Score, Status, Metrics, Runs, Duration. The example values are illustrative. Show the score direction only when known from the evaluation contract.

- Sample displays only its one-based sequence number within this module, linked to its Sample Report. Keep the complete identifier in that detailed report and in the internal report reference. Resolve actual filenames through registered references; the links above are illustrative.
- Runs displays its entire counter as a link to that sample's section in `<module_id>.runs.md`, for example `table_detection.runs.md#sample-002`. Parameters, Score, Status, Metrics, and Duration are plain values, not links.
- Use ordinary digits for sample numbers and run counters, without circled digits, badges, or extra numeric markers.
- Use one Parameters column. Its second header line lists parameters eligible for optimization from ParamSpace, separated by ` / `. Each row lists their values in the same order, with the same separator. Show fixed parameters once in Configuration.
- Use one Metrics column. Its second header line lists evaluation metric names separated by ` / `. Each row lists their values in the same order. A named metric may also be the optimization score; retain it in Metrics. Keep a consistent name order across all rows and use `—` for each missing value. If there are no metrics or no variable parameters, retain the corresponding column and display `—`.
- Metrics are dynamic: derive names and values from the `metrics` mappings returned by `SampleTrial.evaluate()`. Do not hardcode precision, recall, f1, or any other task-specific metric. These names in the example are illustrative only. If different samples return different keys, use their union in a stable order and show `—` for missing values.
- Show failed and running samples as well as successful samples. Missing evaluations are `—`.
- Keep full parameter values available in the sample report when a compact representation is necessary.

### Section 4: Selected Sample

Use this section title in both optimizer modes.

- In `optimize`, identify the best sample selected by the optimizer. During execution or after interruption, label it as the best available result so far.
- In `start_only`, identify the starting sample and explicitly state that no search or comparison between samples was performed. Do not call it the best sample.
- Show the selected sample's sequence number as a link to its Sample Report, its score and returned metrics, and its concrete parameter values. Use the same number as in the Samples table. Link available artifacts.
- Include a domain summary only when supplied by the evaluation/reporting mechanism; do not invent conclusions from numeric scores.
- If no evaluated result is available, state that and explain the known reason. Do not present a failed, unevaluated sample as a selected successful result.

### Section 5: Module Optimization Progress

Include the MD fragment rendered by ModuleOptimizer and submitted through `write_module_optimization_progress`. It belongs in this Module Report, not in a separate Optimization Report file.

In `optimize`, show any available explanation of search progression, parameter-selection decisions, and stopping behavior. Do not invent explanations or duplicate the Samples and Selected Sample sections. If no additional details are supplied, state `No optimization progress details available.`

In `start_only`, retain the section and state `No parameter search was performed.`

### Section 6: Module Artifacts

Link available module-level artifacts, dataset reports, and selected-sample artifacts with clear labels. Per-run artifacts for all samples remain accessible through sample reports rather than an unbounded flat file list here. Sample Reports reside in `samples/` and Run Reports in `runs/`, relative to the job run directory.

Do not link to a separate Optimization Report: optimization content is embedded above. Display `No output files available.` when appropriate.

### Section 7: Errors

Use a Sample / Run / Error / Effect / Details table for sample and run failures, and include module-level failures even when they have no sample or run identifier. Explain whether an error prevented evaluation or interrupted execution. Link available diagnostics. If no errors have been recorded, state `No errors recorded.`

## 4. Mode Differences

| Aspect | No optimizer | start_only | optimize |
|---|---|---|---|
| Execution unit in the report | Single module execution | Starting sample with its runs | Samples with their runs |
| Parameter search | None | Not performed | Performed according to optimizer configuration |
| Sample table | Not applicable | One starting sample once started | All started samples |
| Evaluation | Published module outputs; no synthetic score | Starting sample evaluation | Evaluation of samples |
| Module selected sample | Not applicable | Starting sample result | Optimizer-selected best result |

## 5. Scope and Follow-up

This document defines report presentation without changing execution semantics.
