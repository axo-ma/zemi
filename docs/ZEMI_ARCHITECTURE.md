# ZEMI architecture

Status: normative high-level architecture specification.

This document defines the stable architectural boundaries of ZEMI. Detailed
configuration and adapter contracts remain in the linked specifications.
Keywords **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 1. Universal execution core

The universal containment model is:

`ZEMI Instance → System → Component → Playbook`

- A **ZEMI Instance** is an installed platform root. It owns resources shared
  across components, including runtimes, models, temporary storage, and the
  separate secret store.
- A **System** is one declared execution configuration. It defines system-wide
  values but does not implicitly inject them into descendants.
- A **Component** is a standalone project or functional unit within an Instance.
  It owns its playbooks, configuration, inputs, run directory, and reports.
- A **Playbook** is one executable workflow, currently represented by a
  parameterized notebook. It is the smallest configured workflow unit.

This core is general-purpose. A Component and Playbook MUST remain usable for a
fixed, single execution without requiring model optimization, datasets,
evaluators, or an AI framework.

## 2. Experimental AI-enablement layer

AI experimentation is an explicit layer on top of the universal core. Its
concepts are **ParamSpace**, optional **Arsenal**, **sampler**, **SampleTrial**,
**objective**, the trial hierarchy, and experiment reports. These concepts MUST
NOT redefine Instance, System, Component, or Playbook ownership.

- ParamSpace declares variable dimensions for a Playbook and exists only when
  at least one `values` or `range` dimension is present.
- Arsenal optionally supplies named model endpoints and client integrations.
- A sampler exposes `next_sample(history)` and `best_sample(history)` and keeps
  history as the source of truth.
- SampleTrial is the single public experiment extension point. It owns dataset
  loading, per-item Playbook execution policy, evaluation, result construction,
  and domain-specific Markdown rendering.
- `SampleTrial.evaluate(runs)` returns the complete numeric metrics mapping and
  optional feedback. It receives neither ParamSample nor an artificial completed
  trial wrapper.
- A sampler objective selects one metric and direction. ZEMI derives
  `score = metrics[objective.metric]`; SampleTrial does not receive the objective.
- Trials and reports preserve the executed data flow and outcomes.

DSPy or another optimization framework MAY implement an adapter or sampler, but
no such framework is part of the universal execution contract.

## 3. Parameter scopes and resolution

The four parameter scopes are independent:

- System Params: `system.params`
- Component Params: `component.params`
- Arsenal Params: `arsenals[].params`
- Playbook Params: `playbooks[].params`

There is no automatic inheritance, parent merge, or name-based shadowing across
these scopes. Data moves between scopes only through explicit `ref` references
or `__include__` table composition. Resolution MUST be deterministic, ordered,
deep-copying, and complete before ParamSpace construction or execution.

Parameter value forms have distinct meanings:

- a plain JSON-compatible value is fixed;
- `{ select = [...] }` chooses one fixed value once while the job loads;
- `{ input = ... }` obtains one typed fixed value once while the job loads;
- `{ values = [...], start = ... }` declares a finite variable dimension;
- `{ range = { min, max, step }, start = ... }` declares a numeric variable
  dimension.

`select` and `input` MUST NOT create ParamSpace dimensions. Full schema,
reference, wrapper, and resolution rules are defined by
[ZEMI Params 0.3](ZEMI_PARAMS_0.3.md).

## 4. ParamSpace execution modes

Every Playbook with variable dimensions MUST explicitly choose a
`param_space_mode`:

- `start_only` requires and validates a sampler configuration, but does not
  sample. It executes the fixed and declared `start` values once as an ordinary
  PlaybookRun, without a SampleTrial, dataset, or evaluator.
- `sampler` passes the complete ParamSpace to `[playbooks.sampler]` and executes
  the sampling/optimization lifecycle. It MUST configure a sampler.

A fixed-only Playbook has no ParamSpace and MUST omit both mode and sampler. It
executes once as an ordinary PlaybookRun. A variable ParamSpace MUST NOT
silently fall back to its start sample.

## 5. Trial and evaluation model

The complete sampler-mode experiment hierarchy is:

`JobTrial → PlaybookTrial → SampleTrial → PlaybookRun`

- JobTrial records one Component job.
- PlaybookTrial records one enabled Playbook traversing its ParamSpace.
- SampleTrial records one ParamSample evaluated against the complete dataset.
- PlaybookRun records one Playbook execution for one dataset item.

Fixed-only and `start_only` runs have no SampleTrial. Evaluator results are a
finite numeric metric map plus optional JSON-compatible feedback.

SampleTrial evaluates only after all required PlaybookRuns are collected. Its
result stores sample, runs, scalar score, the full metrics mapping, optional
feedback, statuses, timestamps, failures, and artifacts. Execution failures and
evaluation diagnostics are separate statuses. The objective names one metric
and `maximize` or `minimize`; only the derived score determines ranking.

Reports MUST preserve stable ids, resolved sample parameters, runs, metrics,
feedback, objective values, statuses, timestamps, failures, ranking, and the
selected sampler configuration. Notebook artifacts remain attached to their
PlaybookRuns. Detailed lifecycle and report behavior is defined by
[Dataset optimization](DATASET_OPTIMIZATION.md).

## 6. SampleTrial extension boundary

The public extension is one complete SampleTrial implementation, not separate
dataset/run/evaluator adapters. Canonical configuration selects a built-in such
as `table_detection` or trusted Component code through the confined explicit
form `@comp/path.py:ClassOrFactory`. A custom implementation MUST provide
`load_dataset`, `run`, `evaluate`, `result`, and `render_report`. Internal helpers
may exist inside a built-in or migration bridge, but are not canonical public
configuration. Arbitrary module imports, absolute paths, external entry points,
and expression evaluation are forbidden. See
[Dataset optimization](DATASET_OPTIMIZATION.md).

## 7. Arsenal lifecycle and secrets

An Arsenal is an optional model-access boundary. A playbook may reference one
Arsenal by stable id; without that reference it uses no Arsenal lifecycle or
injected services. Component configuration controls whether a referenced
Arsenal is owned for a job, managed per playbook, or treated as external:

- `job` starts one owned session before its enabled playbooks and stops it after
  the group;
- `playbook` leaves lifecycle management to each Playbook run;
- `external` never starts or stops the external service.

Within an Arsenal session, managed endpoints may own only processes started by
that session; external endpoints are validated but never lifecycle-owned.
Endpoint ownership and protocol details are specified in
[Arsenal endpoints](../ARSENAL_ENDPOINTS.md).

Inputs are universal and independent of Arsenal. In an input specification,
`env` enables persistence/reuse under the neutral Instance store
`@inst/_inputs/values.env`; it never means `os.environ`. Without `env`, input is
ephemeral. `validate` only checks a value and never enables persistence.
`secret = true` only hides entry and masks reports. The four combinations of
ephemeral/persistent and visible/secret are valid. Legacy values in
`@inst/_secrets/arsenal.env` migrate on first reuse. See [Inputs](INPUTS.md).

## 8. Contract maintenance

This file MUST be updated in the same change whenever an architectural contract
above changes. Detailed documents SHOULD link back here and remain the single
source of truth for their lower-level schemas rather than duplicating this
overview. Changes to Params resolution belong in
[ZEMI Params 0.3](ZEMI_PARAMS_0.3.md); SampleTrial and reporting
details belong in [Dataset optimization](DATASET_OPTIMIZATION.md); endpoint and
input behavior belongs in [Inputs](INPUTS.md).
