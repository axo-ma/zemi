# ZEMI architecture

Status: normative high-level architecture specification.

## Universal execution core

`ZEMI Instance → System → Component → Module`

A ZEMI Instance owns shared runtimes, models, temporary storage, and inputs. A
System is one declared execution configuration. A Component is a standalone
project or product-delivery unit within an Instance. A Module is an executable
unit owned by a Component. This core is independent of AI enablement. Playbook
specializes Module (`kind = "playbook"`) and executes a parameterized notebook.
Only this Module kind currently has a runtime.

## Experimental layer

The experimental layer consists of ParamSpace, optional Arsenal,
PlaybookOptimizer, SampleTrial, the trial hierarchy, and reports.

- ParamSpace is constructed solely from resolved `modules.params` and exists
  when at least one `values` or `range` dimension is present.
- Arsenal optionally supplies named model endpoints and client integrations.
- PlaybookOptimizer exposes `next_param_sample(history)` and
  `best_param_sample(history)` and always maximizes finite numeric score.
- TrialDataset owns flat dataset loading, validation, and its cross-history report.
- SampleTrial owns per-item execution, evaluation, result construction, and one
  individual Sample Trial Report. ModuleOptimizer owns Optimization Progress.
- `SampleTrial.evaluate(runs, dataset)` returns `(metrics, score, feedback)`.

DSPy or another framework MAY implement a SampleTrial or optimizer, but is not
part of the universal execution contract.

## Parameter scopes and resolution

System, Component, Arsenal, and Module params are independent. Data crosses
scopes only through explicit `ref` or `__include__`. Resolution is deterministic,
deep-copying, cycle-checked, and complete before ParamSpace construction.

Plain values are fixed. `select` and `input` resolve once to fixed values.
`values/start` and `range/start` create variable dimensions. Full rules are in
[ZEMI Params 0.6](ZEMI_PARAMS_0.6.md).

## Fixed and optimized execution

A fixed Module omits optimizer and runs once. A variable Module configures
`[modules.optimizer]` and a SampleTrial. `mode = "start_only"` performs one
complete start-sample trial; `mode = "optimize"` runs the full loop.

`JobTrial → ModuleTrial → SampleTrial → ModuleRun`

Each Run is the intersection of a SampleTrial and DatasetItem. SampleTrial evaluates after its Module runs are collected. Its result stores
the sample, runs, finite scalar score, complete finite metrics, optional JSON
feedback, statuses, timestamps, failures, and artifacts. Reports preserve those
values, descending score ranking, best params, and optimizer configuration.

## SampleTrial extension boundary

Canonical configuration selects a class via `@comp/path.py:ClassName`. The class
MUST inherit SampleTrial and implement `load_dataset`, `run`, `evaluate`,
`result`, and `render_report`. Absolute paths, external entry points, arbitrary
module imports, and expression evaluation are forbidden. See
[Dataset optimization](DATASET_OPTIMIZATION.md).

## Arsenal, inputs, and secrets

Arsenal is optional. Its `job`, `playbook`, and `external` lifecycle values
control ownership without changing Playbook semantics. Inputs are independent
of Arsenal. Persistent inputs use `@inst/_inputs/values.env`; secret values are
masked in reports. See [Inputs](INPUTS.md) and
[Arsenal endpoints](../ARSENAL_ENDPOINTS.md).

## Contract maintenance

Changes to the parameter architecture MUST update this file and
[ZEMI Params 0.6](ZEMI_PARAMS_0.6.md). SampleTrial and report details belong in
[Dataset optimization](DATASET_OPTIMIZATION.md).
