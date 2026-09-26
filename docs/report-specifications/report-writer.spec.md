# ReportWriter: Report Files and MD Fragments

Status: accepted for implementation.
Date: 2026-09-24.

## 1. Ownership and Lifecycle

One ReportWriter is created for the entire job execution and shared by all modules and reporting clients. It owns report file locations, navigation, fragment placement, and file updates.

Clients produce MD fragments: Markdown strings representing parts of a report, not necessarily standalone documents. Each client controls the layout inside its fragment, either through DefaultReportRenderer or a custom rendering method. ReportWriter does not derive domain-specific layouts from metrics or other raw results.

Each fragment type has a dedicated ReportWriter method. The method receives routing identifiers and an `md_fragment`. Rewriting the same fragment replaces its previous version; it does not append a duplicate. Distinct samples and runs have distinct fragment identities.

ModuleOptimizer is the optimizer. These names do not represent separate entities; this specification consistently uses ModuleOptimizer.

## 2. Report File Registry

Paths below are relative to the job run directory. Sample Reports reside in `samples/` and Run Reports in `runs/`; they do not reside at the top level. Worksheet Detection Reports reside in `dataset-items/`. Other report files remain at the top level. File naming uses safe, collision-resistant registered references; artifact paths are recorded by ReportWriter rather than reconstructed by clients.

| Report type | Filename | Cardinality | Without optimizer | start_only | optimize |
|---|---|---|---|---|---|
| Job Report | `index.md` | One per job execution | Yes | Yes | Yes |
| Module Report | `<module_id>.md` | One per configured module | Yes | Yes | Yes |
| Module Runs Report | `<module_id>.runs.md` | One per module with an optimizer | No | Yes | Yes |
| Sample Report | `samples/<module_id>.sample-<sample_id>.md` | One per started sample | No | Starting sample | Each started sample |
| Run Report | `runs/<module_id>.run-<run_id>.md` | One per started run | Single run when started | Each started run | Each started run |
| Dataset Report | `<module_id>.dataset.md` | One per module with a trial dataset | No | Yes | Yes |
| Review Report | `<module_id>.review.md` | One per module configured for review | No | When configured | When configured |
| Worksheet Detection Report | `dataset-items/<module_id>.<item_file_key>.md` | One per dataset item per module | No | Yes | Yes |


Use actual identifiers; do not infer that sample or run IDs are numeric. The exact filesystem-safe encoding and collision handling for identifiers remain to be specified. Distinct report identities must never resolve to the same file, including collisions with `index.md` or generated report suffixes.

HTML, IPYNB, and other execution artifacts are not additional MD report types. Link to their actual generated locations; this registry does not change their paths.

Create Job and Module Reports early enough to represent unstarted modules. Create Sample and Run Reports when the corresponding execution starts, so failures are reportable. Create applicable Dataset Reports during initialization with an explicit pending state if their content is not yet available. Do not emit links to files that do not exist.

## 3. Navigation and Common Envelope

ReportWriter supplies a common document envelope: report title, owning job/module/sample/run identifiers as applicable, and relative navigation links. Clients supply the report content through the fragment methods below. A content fragment must not duplicate the document-level title or navigation envelope.

- Job Report links to Module Reports.
- Module Report without an optimizer links to its single Run Report and directly to available output HTML/IPYNB.
- Module Report with an optimizer links to Sample Reports and the Dataset Report when applicable. Module Optimization Progress are included in the Module Report itself.
- Sample Report links to its Run Reports and back to its Module Report.
- Run Report links to its Sample Report when owned by a sample, otherwise directly to its Module Report. It also links to its Module Report and Job Report where useful.
- Dataset Reports link back to their Module Report.
- Review Reports link to their Module and Job Reports; Module artifacts link to Review Reports. `write_review_report(module_id, md_fragment)` replaces the review fragment. See [Review Report](review-report.spec.md) for its launch-time source snapshot.
- Every report is identifiable when opened directly and provides a relative link to the Job Report: `index.md` from the root, or `../index.md` from `samples/`, `runs/`, and `dataset-items/`. Resolve all other navigation relative to the source report location as well.

Parent/run link lists are managed navigation, not domain layouts inferred by ReportWriter.

## 4. MD Fragment Registry

The method names and producer assignments below are contracts. Every method takes `md_fragment` in addition to the routing identifiers shown.

| MD fragment | Producer | Target report | Writer method | Routing identifiers |
|---|---|---|---|---|
| Job header | Job executor | Job | `write_job_header` | None |
| Module summary | Job executor | Job | `write_module_summary` | None |
| Module header | Module executor | Module | `write_module_header` | module_id |
| Module parameters | Module executor | Module, without optimizer | `write_module_parameters` | module_id |
| Module results | Module executor | Module, without optimizer | `write_module_results` | module_id |
| Module optimization config | ModuleOptimizer | Module, with optimizer | `write_module_optimization_config` | module_id |
| Module execution summary | Module executor | Module, with optimizer | `write_module_execution_summary` | module_id |
| Module samples summary | ModuleOptimizer | Module, with optimizer | `write_module_samples_summary` | module_id |
| Module selected sample | ModuleOptimizer | Module, with optimizer | `write_module_selected_sample` | module_id |
| Module artifact links | Module executor | Module | `write_module_artifact_links` | module_id |
| Module errors | Module executor | Module | `write_module_errors` | module_id |
| Sample trial report | SampleTrial | Sample | `write_sample_trial` | module_id, sample_id |
| Run report | Run execution client | Run | `write_run_report` | module_id, run_id; sample_id when applicable |
| Module runs summary | Module executor | Module Runs | `write_module_runs_summary` | module_id |
| Dataset report | TrialDataset | Dataset | `write_trial_dataset` | module_id |
| Worksheet detection report | TrialDataset | Worksheet Detection | `write_worksheet_detection_report` | module_id, item_id |
| Module optimization progress | ModuleOptimizer | Module, with optimizer | `write_module_optimization_progress` | module_id |

`write_module_summary` receives the entire module summary fragment, including both execution groups for a mixed job. It is not one fragment per module.

Module artifact links populate the Module Artifacts section (formerly Output Files) through `write_module_artifact_links` and `render_module_artifact_links`. This section lists files and report links. It is distinct from Module Optimization Progress, which is supplied by ModuleOptimizer through `write_module_optimization_progress` and `render_module_optimization_progress` and describes the search process.

Module summary links Samples to the Module Report's `samples` anchor and Runs to the separate Module Runs Report at `<module_id>.runs.md`. Result links to the selected sample's existing Sample Report. Module execution summary contains only aggregate counters and a link to Module Runs Report, not a run listing. Default and custom rendering must preserve these destinations, including empty states.

The four ModuleOptimizer fragments have distinct responsibilities:

| MD fragment | Content |
|---|---|
| Module optimization config | ModuleOptimizer settings, execution mode, parameter search space, and limits |
| Module samples summary | Table of samples with parameter values, statuses, scores, and report links |
| Module selected sample | Selected sample, its parameter values and score; the starting sample in `start_only` |
| Module optimization progress | Search steps, explanations supplied by ModuleOptimizer, and stopping reason |

Module samples summary presents results; Module optimization progress explains how the search proceeded when ModuleOptimizer provides that information. Do not invent explanations when none are supplied. All four fragments belong to the Module Report.

Module header contains execution metadata; ReportWriter's envelope supplies the title and navigation. Sample, Run, and Dataset content fragments may each contain multiple headings, tables, and narrative sections under their shared envelope. Their internal layouts will be defined in separate report specifications, with task-specific layouts allowed where appropriate.

## 5. Fragment Placement

### Job Report

1. Job header.
2. Module summary.

### Module Report Without an Optimizer

1. Module header.
2. Module parameters.
3. Module results.
4. Module artifact links.
5. Module errors.

### Module Report With an Optimizer

1. Module header.
2. Module optimization config.
3. Module execution summary.
4. Module samples summary.
5. Module selected sample.
6. Module optimization progress.
7. Module artifact links.
8. Module errors.

### Sample, Run, and Dataset Reports

Common envelope followed by the corresponding content fragment. Managed navigation may be updated independently of the content fragment as children are created.

## 6. Write Semantics

- Fragment identity is its method/type plus its owning module and sample/run identifiers where applicable. sample_id on a Run Report establishes parentage, not a second identity for the same run.
- Methods for different fragments preserve each other's content. Final section order is fixed by the report layout, not by call order.
- Each write updates the target Markdown file. Readers must see a complete previous or current version, not a partially written file.
- A write returns a report reference identifying the target report. A reference to a child report does not imply execution success.
- Missing fragments remain explicitly pending or unavailable according to the report layout. Missing scores are not rendered as zero.
- No automatic changes to other reports are implied by a fragment write. Their owners submit updated summaries when state changes; ReportWriter maintains managed navigation separately.
- Clients must redact secret values before submitting Markdown. ReportWriter must not persist known secret values in report content.
- Renderer-specific assumptions about HTML support must not be required for basic navigation or reading ordinary Markdown tables.

## 7. Mode Semantics

Without an optimizer, the single module run receives a Run Report, with no synthetic sample or optimization score.

In start_only, SampleTrial and TrialDataset reports remain applicable. Selected Sample describes the starting sample, not a winner of a search. Module Optimization Progress state `No parameter search was performed.`

In optimize, ModuleOptimizer supplies the samples summary, selected result, and Module Optimization Progress within the Module Report. No separate Optimization Report file is created in any mode. The `write_module_optimization_progress` method updates the module fragment and returns a reference to the Module Report. During execution, selection is provisional. Failures retain available reports and artifacts.

## 8. Default Rendering and Overrides

DefaultReportRenderer supplies a default implementation for every MD fragment type. A usable report must not require a custom renderer. It renders data into Markdown strings and does not write files, choose output paths, or create a ReportWriter.

| Fragment | DefaultReportRenderer method |
|---|---|
| Job header | `render_job_header` |
| Module summary | `render_module_summary` |
| Module header | `render_module_header` |
| Module parameters | `render_module_parameters` |
| Module results | `render_module_results` |
| Module optimization config | `render_module_optimization_config` |
| Module execution summary | `render_module_execution_summary` |
| Module samples summary | `render_module_samples_summary` |
| Module selected sample | `render_module_selected_sample` |
| Module artifact links | `render_module_artifact_links` |
| Module errors | `render_module_errors` |
| Sample trial report | `render_sample_trial` |
| Run report | `render_run_report` |
| Module runs summary | `render_module_runs_summary` |
| Dataset report | `render_trial_dataset` |
| Worksheet detection report | `render_worksheet_detection_report` |
| Module optimization progress | `render_module_optimization_progress` |

Each rendering method receives the data needed for its fragment and returns an `md_fragment` string. Exact input signatures will be defined with the corresponding report contracts. Defaults follow the agreed layouts and explicitly represent missing, pending, empty, and failed results without inventing values. Structured values must remain readable and secret values must be masked.

### TrialDataset and SampleTrial Overrides

The base TrialDataset and SampleTrial implementations provide `render_report` methods that delegate to DefaultReportRenderer. Users can override `render_report` in their own TrialDataset or SampleTrial subclass to replace the layout of that client's fragment. Overriding one fragment does not require implementing the other default methods or replacing ReportWriter.

Illustrative delegation, with input signatures subject to the existing class contracts:

```python
class TrialDataset:
    def render_report(self, history):
        return self.report_renderer.render_trial_dataset(
            dataset=self,
            history=history,
        )


class SampleTrial:
    def render_report(self, runs, metrics, score, feedback):
        return self.report_renderer.render_sample_trial(
            sample_trial=self,
            runs=runs,
            metrics=metrics,
            score=score,
            feedback=feedback,
        )


class TableDetectionSampleTrial(SampleTrial):
    def render_report(self, runs, metrics, score, feedback):
        md_fragment = ...  # Task-specific Markdown layout.
        return md_fragment
```

`report_renderer` defaults to DefaultReportRenderer. Other fragment producers use its corresponding methods unless they provide a custom rendering implementation. Renderer construction/injection is separate from the single job-scoped ReportWriter lifecycle.

Custom renderers may also call the default implementation and extend its returned fragment. Custom fragments retain the same placement, ownership, secret-redaction requirements, and writer-managed title/navigation envelope as default fragments.

The reporting flow is:

1. A client receives execution or evaluation data.
2. Its rendering method produces an MD fragment using the default implementation or a user override.
3. The caller submits that fragment to the corresponding ReportWriter method.
4. ReportWriter updates the destination file while preserving the other fragments.

## 9. Related Specifications and Remaining Work

- [Job Report](job-report.spec.md).
- [Module Report](module-report.spec.md).
- Sample and Run defaults are provided by DefaultReportRenderer. Dataset and Worksheet Detection layouts are defined in `dataset-report.spec.md` and `worksheet-detection-report.spec.md`.
- Exact identifier encoding, registration API, and integration with execution lifecycle remain implementation design items.

This is the implemented architecture and file/fragment registry.
