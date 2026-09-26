# Reporting Architecture Specification

Status: accepted for implementation.
Date: 2026-09-24.

## 1. Scope

This document defines the ZEMI reporting architecture.

## 2. Components and Responsibilities

| Component | Responsibility |
|---|---|
| Reporting client | Owns execution or evaluation data and requests rendering of its report fragments |
| DefaultReportRenderer | Provides default Markdown rendering for every registered fragment type |
| Custom rendering method | Defines task-specific fragment content and layout, optionally extending a default implementation |
| MD fragment | A Markdown string representing part of a report; it is not necessarily a standalone document |
| ReportWriter | Places fragments in reports, manages report files and navigation, and persists updates |

ModuleOptimizer denotes the optimizer; it is not a second entity alongside an optimizer.

## 3. Job-scoped Writer

Create exactly one ReportWriter for each job execution. Share it with all modules and reporting clients in that execution. Do not instantiate a writer per module, sample, or run.

The writer knows the job run directory. Calls carry the owning module, sample, and run identifiers as appropriate. Job run ID is the actual directory name, for example `run260923-143000`.

## 4. Rendering Contract

Clients control the layout inside their MD fragments. The writer does not infer domain layouts from raw metrics or results.

DefaultReportRenderer provides a method for every fragment type, so standard reports work without custom rendering code. Base TrialDataset and SampleTrial `render_report` methods delegate to this renderer. Users may override `render_report` in their own TrialDataset and SampleTrial subclasses to return task-specific Markdown. They may also extend the default result rather than replacing it entirely.

Rendering returns Markdown and does not write files. Custom and default fragments use the same writer contract. Writer-managed document titles, identifiers, and navigation remain outside the custom fragment.

## 5. Reporting Flow

1. A reporting client obtains execution or evaluation data.
2. A default or overridden rendering method produces an MD fragment.
3. The caller submits it to the dedicated ReportWriter method for that fragment type.
4. ReportWriter replaces the previous version of that fragment and updates the corresponding file.

Illustrative call sequence:

```python
# Once per job execution.
report_writer = ReportWriter(run_directory=run_directory)

# During execution of a sample; this method may be user-defined.
md_fragment = sample_trial.render_report(runs, metrics, score, feedback)
report_ref = report_writer.write_sample_trial(
    module_id=module_id,
    sample_id=sample_id,
    md_fragment=md_fragment,
)
```

This is a snapshot update model: repeated writes replace a fragment rather than appending duplicate content. Fragment order follows the report layout, not call order. Updating one fragment preserves the others. Readers must not observe partially written report files.

## 6. Report Types and Navigation

The report types are Job, Module, Module Runs, Sample, Run, Dataset, Dataset Item, and automatic Reproduction. Job, Module, Module Runs, Dataset, and Reproduction Reports reside in the root of the job run directory. Sample Reports reside in `samples/`, Run Reports in `runs/`, and Dataset Item Reports in `dataset-items/`. Output IPYNB files are execution artifacts linked from reports, not additional Markdown report types. Automatic notebook HTML export is removed.

Job Reports link to Module Reports. Module Reports link to the applicable Run or Sample Reports and Dataset Reports. Sample Reports link to their Run Reports. Every detailed report remains identifiable when opened directly and provides navigation back to the job report.

Without an optimizer, a module has one run and no synthetic sample. With `start_only`, the starting sample and its runs are reported without an optimization search report. With `optimize`, the samples, runs, dataset, and optimization process are reported. ModuleOptimizer renders a Module Optimization Progress fragment inside the Module Report; there is no separate Optimization Report file.

## 7. Detailed Contracts

Dataset and Dataset Item Reports use universal targets, comparison predictions and dynamic metrics. See dataset-report.spec.md, dataset-item-report.spec.md and sample-report.spec.md. Task adapters supply comparison_prediction when the compared value differs from the full output.

When preparing implementation tasks, also include the agreed [table detection set-semantics requirement](table-detection-evaluation.spec.md): repeated identical predicted ranges must be deduplicated before evaluation and must not reduce detection success or scores.

The module-wide run listing is a separate Module Runs Report at `<module_id>.runs.md`, in addition to individual Run Reports. Module Reports contain only the Runs counter/link, not the listing. Job Report Runs links also target this separate report. See [Module Runs Report](module-runs-report.spec.md).

The following documents are the sources for detailed contracts; this architecture document does not duplicate their registries:

- [Report files, MD fragments, writer methods, and default rendering](report-writer.spec.md).
- [Job Report layout](job-report.spec.md).
- [Module Report layouts](module-report.spec.md).

Sample and Run defaults are defined by the renderer. Dataset and Dataset Item layouts are specified here.
