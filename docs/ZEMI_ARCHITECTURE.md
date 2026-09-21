# ZEMI architecture

Status: normative high-level architecture specification.

## Universal execution core

`ZEMI Instance → System → Component → Playbook`

A ZEMI Instance owns shared runtimes, models, temporary storage, and inputs. A
System is one declared execution configuration. A Component is a standalone
project within an Instance. A Playbook is one executable workflow, currently a
parameterized notebook. Fixed Playbooks remain usable without a model, dataset,
optimizer, or Arsenal.

## Experimental layer

The experimental layer consists of ParamSpace, optional Arsenal,
PlaybookOptimizer, SampleTrial, the trial hierarchy, and reports.

- ParamSpace is constructed solely from resolved `playbooks.params` and exists
  when at least one `values` or `range` dimension is present.
- Arsenal optionally supplies named model endpoints and client integrations.
- PlaybookOptimizer exposes `next_param_sample(history)` and
  `best_param_sample(history)` and always maximizes finite numeric score.
- SampleTrial is the one public experiment extension. It owns dataset loading,
  per-item execution, evaluation, result construction, and domain Markdown.
- `SampleTrial.evaluate(runs)` returns `(metrics, score, feedback)`.

DSPy or another framework MAY implement a SampleTrial or optimizer, but is not
part of the universal execution contract.

## Parameter scopes and resolution

System, Component, Arsenal, and Playbook params are independent. Data crosses
scopes only through explicit `ref` or `__include__`. Resolution is deterministic,
deep-copying, cycle-checked, and complete before ParamSpace construction.

Plain values are fixed. `select` and `input` resolve once to fixed values.
`values/start` and `range/start` create variable dimensions. Full rules are in
[ZEMI Params 0.5](ZEMI_PARAMS_0.5.md).

## Fixed and optimized execution

A fixed-only Playbook omits optimizer and runs once. A variable Playbook MUST
configure `[playbooks.optimizer]` and a SampleTrial and always executes the
optimization lifecycle. There is no separate ParamSpace mode.

`JobTrial → PlaybookTrial → SampleTrial → PlaybookRun`

SampleTrial evaluates after its PlaybookRuns are collected. Its result stores
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
[ZEMI Params 0.5](ZEMI_PARAMS_0.5.md). SampleTrial and report details belong in
[Dataset optimization](DATASET_OPTIMIZATION.md).
