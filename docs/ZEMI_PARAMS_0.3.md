# ZEMI Params 0.3

Status: normative specification implemented by ZEMI Params 0.3.

The complete machine-readable example is maintained separately at
[`../examples/params/full_params_0.3.toml`](../examples/params/full_params_0.3.toml).

This document defines the configuration model and execution semantics for ZEMI
Params 0.3. Keywords **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are
normative.

The containing system boundaries are defined by
[ZEMI architecture](ZEMI_ARCHITECTURE.md).

## 1. Goals and boundaries

ZEMI Params 0.3 separates ZEMI-owned structure from user-owned values, makes
parameter reuse explicit, and gives parameter search a stable trial model. The
term `pipeline` is replaced by `system`.

ZEMI-owned fields live only in closed structural sections. Arbitrary user keys
are allowed only below a `params` section. The four user parameter scopes are:

- `system.params`
- `component.params`
- `arsenals[].params`
- `playbooks[].params`

There is no automatic inheritance between these scopes. Values move between
scopes only through `ref` or `__include__`.

The sampler owns proposal and observation state; there is no separate optimizer
configuration object. DSPy MAY be used inside a notebook/playbook or a sampler
adapter, but ZEMI MUST NOT require `dspy.Module`, `forward`, or any other DSPy
program shape.

## 2. Canonical document shape

The canonical top-level keys are `system`, `component`, `arsenals`, and
`playbooks`. A Params 0.3 document MUST contain `system.version = "0.3"`, one
`[component]`, zero or more `[[arsenals]]`, and one or more `[[playbooks]]`.

Every structural table is closed: an unknown built-in key is an error. The
contents of its `params` child are open and JSON-compatible, subject to the
wrapper rules below.

### 2.1 Full example

```toml
[system]
version = "0.3"

[system.params]
locale = "en"

[system.params.defaults]
temperature = 0.2
max_tokens = 512

[component]
name = "two-arsenal-search"
stop_on_error = true

[component.params]
dataset_root = "@comp/data"

[component.params.generation]
__include__ = { ref = "system.params.defaults" }
max_tokens = 768

[[arsenals]]
id = "local"
config_path = "@comp/zemi/llm_curated_set_model_mode.toml"
lifecycle = "job"

[arsenals.params]
device = "cpu"
generation = { ref = "component.params.generation" }

[[arsenals]]
id = "remote"
config_path = "@comp/zemi/llm_external_providers.toml"
lifecycle = "external"

[arsenals.params]
device = "remote"

[[playbooks]]
id = "summarize-local"
path = "playbook.ipynb"
arsenal = "local"
enabled = true
param_space_mode = "sampler"

[playbooks.params]
__include__ = { ref = "component.params.generation" }
locale = { ref = "system.params.locale" }
device = { ref = "arsenals.local.params.device" }
temperature = { values = [0.0, 0.2, 0.5], start = 0.2 }
prompt_style = { values = ["brief", "detailed"], start = "brief" }

[playbooks.sampler]
strategy = "grid"
max_samples = 6
seed = 17

[playbooks.sampler.objective]
metric = "f1"
direction = "maximize"

[playbooks.sampler.sample_trial]
implementation = "table_detection"
path = "@comp/data/eval.json"

[playbooks.sampler.sample_trial.params]
language = { ref = "system.params.locale" }

[[playbooks]]
id = "extract-remote"
path = "playbook_gbnf.ipynb"
arsenal = "remote"
enabled = true
param_space_mode = "sampler"

[playbooks.params]
__include__ = [
  { ref = "system.params.defaults" },
  { ref = "component.params.generation" },
]
device = { ref = "arsenals.remote.params.device" }
schema_mode = { values = ["strict", "repair"], start = "strict" }
temperature = { range = { min = 0.0, max = 0.4, step = 0.2 }, start = 0.0 }

[playbooks.sampler]
strategy = "block_coordinate"
max_samples = 5
seed = 23
blocks = [["schema_mode", "temperature"]]

[playbooks.sampler.objective]
metric = "f1"
direction = "maximize"

[playbooks.sampler.sample_trial]
implementation = "@comp/sample_trials/extraction.py:ExtractionSampleTrial"
path = "@comp/data/extraction.csv"

[playbooks.sampler.sample_trial.params]
input_column = "text"
```

`arsenals.local.params.device` is a logical named lookup. Arrays remain the TOML
representation, but `arsenals.<id>` and `playbooks.<id>` are valid reference
namespaces. Duplicate ids are therefore forbidden.

## 3. Built-in fields

### 3.1 `system`

- `version` (required string): schema version; Params 0.3 accepts exactly
  `"0.3"`.
- `params` (optional table): system-wide user values. Its presence does not
  cause inheritance.

### 3.2 `component`

- `name` (optional non-empty string): logical component name; defaults to the
  component directory name.
- `stop_on_error` (optional boolean, default `true`): stop the enclosing job
  after the first failed trial.
- `params` (optional table): component user values.

### 3.3 `arsenals[]`

- `id` (required non-empty string): document-unique stable identifier. It MUST
  match `[A-Za-z][A-Za-z0-9_-]*`.
- `config_path` (optional ZEMI path string): Arsenal configuration. It is
  required when `lifecycle = "job"`.
- `lifecycle` (optional enum, default `"playbook"`): `job` starts once before
  the Arsenal's playbooks and stops once afterwards; `playbook` lets each run
  manage its own session; `external` never starts or stops Arsenal processes.
- `params` (optional table): Arsenal user values. Arsenal built-ins and user
  values MUST NOT share a table.

### 3.4 `playbooks[]`

- `id` (required non-empty string): document-unique stable identifier using the
  same syntax as Arsenal ids.
- `path` (required non-empty relative path or `@comp/...` path): source notebook.
- `arsenal` (optional string): exact Arsenal id. When present, the referenced
  Arsenal MUST exist. When absent, the Playbook runs without Arsenal lifecycle
  management or injected Arsenal service configuration.
- `enabled` (optional boolean or `select` wrapper, default `true`).
- `param_space_mode` (conditionally required enum or `select` wrapper): exactly
  `"start_only"` or `"sampler"`. It MUST be present when `params` resolves to
  one or more `values`/`range` dimensions or when `sampler` is configured. A
  `select` wrapper is resolved once while loading the job and becomes the fixed
  mode; it never creates a ParamSpace dimension.
- `params` (optional table): user parameters and the playbook `ParamSpace`.
- `sampler` (conditionally required table): sampling policy. It is required for
  every variable ParamSpace, in both execution modes.

`param_space_mode = "start_only"` validates the sampler policy but performs no
sampling. It executes one ordinary PlaybookRun with the `start` value of every
variable dimension plus every fixed parameter; it creates no SampleTrial and
loads no dataset. `param_space_mode = "sampler"` passes the complete
ParamSpace to the configured sampler and executes its SampleTrial lifecycle.

When all resolved playbook parameters are fixed, ZEMI creates no ParamSpace and
both `param_space_mode` and `sampler` MUST be absent. The Playbook executes once
as an ordinary run. There is no implicit fallback for a variable ParamSpace:
omitting either its mode or sampler is an error.

### 3.5 `sampler`

- `strategy` (required enum): `grid`, `random`, `coordinate`, or
  `block_coordinate`.
- `max_samples` (optional positive integer): hard proposal limit. It is required
  for `random`, `coordinate`, and `block_coordinate`; for `grid` omission means
  the complete finite Cartesian product.
- `seed` (optional integer): reproducibility seed.
- `blocks` (required non-empty array only for `block_coordinate`): each item is
  a non-empty array of unique variable-dimension names. Names MUST exist in the
  resolved ParamSpace, MUST NOT name fixed parameters, and MUST NOT occur in
  more than one block. Unlisted variable dimensions become singleton blocks in
  declaration order.
- `objective` (required in `sampler` mode): `metric` selects one key from the
  complete SampleTrial metrics mapping; `direction` is `maximize` or `minimize`.
- `sample_trial` (required only in `sampler` mode): one complete built-in or
  custom SampleTrial implementation. It MAY be omitted in `start_only` mode.

An implementation MAY use another optimization framework internally, but the
public sampler contract remains `next_sample(history)` / `best_sample(history)`.

### 3.6 `sample_trial`

- `implementation` (required string): built-in `table_detection` or confined
  trusted Component code `@comp/path.py:ClassOrFactory`.
- `path` (optional ZEMI path): domain data source interpreted by the selected
  implementation.
- `params` (optional table): implementation-owned JSON-compatible configuration.

The stable SampleTrial methods are `load_dataset`, `run`, `evaluate`, `result`,
and `render_report`. Standard `run` executes the Playbook once per DatasetItem;
special workbook/context behavior belongs inside the table implementation.
There is no canonical dataset, run, or evaluator adapter block.

## 4. Parameter values and ParamSpace

A key below any `params` table is a user parameter. A plain JSON-compatible
value is fixed. In `playbooks[].params`, either of these exact wrappers declares
a variable dimension:

```toml
x = { values = [1, 2, 3], start = 2 }
y = { range = { min = 0.0, max = 1.0, step = 0.1 }, start = 0.5 }
```

`values` MUST be a non-empty array of unique JSON-compatible values and `start`
MUST equal one member with type-sensitive equality. `range` MUST contain exactly
`min`, `max`, and `step`; all are finite numbers, `step > 0`, `max >= min`, and
`start` MUST lie on the inclusive generated grid. The wrapper MUST contain
exactly its domain key and `start`. This keeps the start value beside the domain
description.

`ParamSpace` exists only when at least one variable dimension remains after
reference resolution; its declaration order is preserved. A fixed-only
Playbook has no ParamSpace. `ParamSample` is one immutable mapping of every
resolved playbook parameter to a concrete value. Fixed values are included in
every ParamSample for variable Playbooks.

For example, a troubleshooting run can retain the complete search space while
executing only its declared start point:

```toml
[[playbooks]]
id = "detect"
path = "detect.ipynb"
arsenal = "local"
param_space_mode = "start_only"

[playbooks.params]
threshold = { range = { min = 0.1, max = 0.9, step = 0.1 }, start = 0.5 }

[playbooks.sampler]
strategy = "grid"
```

The mode itself can be chosen interactively without adding a dimension:

```toml
param_space_mode = { select = ["start_only", "sampler"] }
```

The existing interactive wrappers remain distinct:

- `{ select = [...] }` chooses one value once while loading the job and does not
  create a search dimension.
- `{ input = ... }` obtains one typed value once while loading the job.

They MAY appear in any `params` table. They are resolved before construction of
the ParamSpace.

## 5. Explicit references and includes

`{ ref = "dotted.path" }` copies one scalar, array, or table. `__include__`
accepts one ref wrapper or a non-empty ordered array of ref wrappers; every ref
MUST resolve to a table. References MAY target only a `params` table or a value
below one. Structural fields such as `component.stop_on_error` and
`arsenals.local.config_path` cannot be referenced or included.

Resolution is deterministic:

1. Parse TOML and validate the closed structural shape and unique ids.
2. Build named `arsenals.<id>` and `playbooks.<id>` lookup namespaces.
3. Resolve `system.params`.
4. Resolve `component.params`.
5. Resolve each `arsenals[].params` in document order.
6. Resolve each `playbooks[].params` and `sample_trial.params` in document order.
7. Within one table, apply `__include__` entries left-to-right; later includes
   replace earlier keys, then local keys replace all included keys.
8. Resolve `select` and `input` once, including `param_space_mode`, then validate
   variable wrappers and mode/sampler consistency. Construct ParamSpace only
   when variable dimensions exist.
9. For fixed-only Playbooks, execute one ordinary PlaybookRun. In `start_only`
   mode validate the sampler and execute the start values as one ordinary
   PlaybookRun; in `sampler` mode pass ParamSpace to the configured sampler.
10. For each sampler proposal, overlay only the sampled dimension values onto the fixed
   resolved playbook params.

References are deep-copied. Missing paths, traversal through non-tables, cycles,
references to structural fields, and malformed wrappers are errors. No parent
scope is merged implicitly; the same key in two scopes is unrelated unless a
ref/include explicitly connects them.

## 6. Trials and execution lifecycle

The full sampler-mode hierarchy is:

`JobTrial → PlaybookTrial → SampleTrial → PlaybookRun`

- `JobTrial`: one execution of a component parameter document.
- `PlaybookTrial`: one enabled playbook traversing its ParamSpace.
- `SampleTrial`: one ParamSample evaluated against the complete dataset.
- `PlaybookRun`: one execution of that playbook for one dataset item.

Fixed-only and `start_only` executions stop at an ordinary PlaybookRun beneath
the PlaybookTrial; they do not synthesize SampleTrial or dataset-item records.

For each `sampler` PlaybookTrial the conceptual outer loop is:

```text
sample = sampler.next_sample(history)
runs = sample_trial.run(playbook, sample, dataset)
metrics, feedback = sample_trial.evaluate(runs)
score = metrics[sampler.objective.metric]
history.append(sample_trial.result(sample, runs, score, metrics, feedback))
```

SampleTrial evaluation runs only after every required PlaybookRun has been
collected. `evaluate` receives runs and its own configuration, not ParamSample
or an artificial completed-trial wrapper. History stores sample, runs, scalar
score, the full metrics mapping, optional feedback, status, timestamps, errors,
and artifacts. Execution status and evaluation diagnostics remain distinct.

Strategy minimum semantics:

- `grid`: deterministic Cartesian product in parameter declaration order, with
  each domain's declared order; the start sample is proposed first.
- `random`: seeded sampling without replacement for finite discrete spaces; the
  start sample is first.
- `coordinate`: start sample first, then vary one dimension at a time around the
  best observed sample; ties keep the earlier sample.
- `block_coordinate`: start sample first, then visit the configured named blocks
  in order. For one block it proposes the Cartesian product of that block's
  domains around the current best sample while every dimension outside the block
  remains fixed at its current-best value. Unlisted dimensions are visited as
  singleton blocks after the explicit blocks, in declaration order.

History is the source of truth; the sampler does not duplicate best-sample
state. A trial stops when the finite grid is
exhausted, `max_samples` is reached, or the sampler reports exhaustion. Objective
comparison uses only the derived scalar score and objective direction. Missing,
boolean, NaN, or infinite selected metrics fail the SampleTrial.

## 7. Validation and reporting

Validation MUST happen before starting an Arsenal or executing a notebook.
Errors MUST name the full logical path and, for array items, the source index or
id. Besides the field rules above, ZEMI MUST reject:

- unknown top-level or structural keys;
- arbitrary keys outside `params`;
- duplicate Arsenal or playbook ids;
- unknown Arsenal references;
- absolute filesystem paths in configuration;
- unsupported strategy, direction, SampleTrial implementation, or parameter wrapper;
- malformed sampler blocks, unknown or fixed block members, or a dimension
  repeated across blocks;
- a variable ParamSpace without `param_space_mode` or without `sampler`;
- sampler mode without `sample_trial` or sampler `objective`;
- invalid built-in or non-confined custom SampleTrial implementation;
- a fixed-only Playbook with `param_space_mode` or `sampler`;
- duplicate ParamSamples proposed by a sampler;
- SampleTrial metrics without the objective metric.

`report.json` is the stable generic machine-readable report and replay source.
Every SampleTrial records sample, runs, score, the complete metrics mapping,
feedback, status, timestamps, errors, and artifacts. ZEMI owns the generic
Markdown shell; SampleTrial owns domain Markdown through `render_report`, called
after every completed sample so later failures do not discard prior results.

## 8. Migration from the current implementation

The implementation preceding 0.3 already supports `ref`, `__include__`,
`select`, `input`, `each`, `pipeline_params`, `component_params`, top-level
`playbooks_params`, nested `arsenals.playbooks_params`, and
`playbook_params`. Migration MUST preserve the proven resolver behavior while
moving to the canonical names and trial hierarchy.

Canonical mappings are:

- `pipeline_params` → `system.params`
- user entries in `component_params` → `component.params`; known lifecycle
  fields → `component`
- `arsenals[].name` → `arsenals[].id`
- `arsenal_config_path` → `config_path`
- `arsenal_start_and_stop_at_job_level = true` → `lifecycle = "job"`
- `playbooks_params` / `arsenals[].playbooks_params` → top-level `playbooks`
- `playbook_name` → `path` (and a generated stable `id` only in a migration
  tool, never silently at canonical validation time)
- `playbook_params` → `params`
- `{ each = [...] }` → `{ values = [...], start = <explicit value> }`

Migration tools and manual migrations MUST also choose an explicit
`param_space_mode` whenever the migrated playbook contains `values` or `range`.
Use `"start_only"` together with a sampler policy to preserve a deliberate
single-start troubleshooting run, or `"sampler"` with sampler `objective` and a
full `sample_trial` contract for search. Legacy dataset/run/evaluator blocks are
accepted only through an explicit compatibility normalization; canonical files
MUST select one SampleTrial implementation. Earlier behavior
where a variable ParamSpace without a sampler silently ran only its start sample
is intentionally rejected because it hid configuration mistakes.

The 0.3 loader SHOULD accept the old complete document shape during one
compatibility window, normalize it before canonical validation, and emit a
clear deprecation warning. A document MUST NOT mix canonical and legacy shapes.
Because legacy `each` lacks a start value, automatic runtime normalization MUST
use its first element and warn; a source migration tool MUST write that choice
explicitly.

## 9. Explicit unresolved schema reminder

The current TOML schema mixes ZEMI built-in fields and arbitrary user values.
This boundary must be formalized through closed structural sections and open
`params` sections, not left implicit. Params 0.3 specifies that boundary; code,
examples, templates, and future extensions must not weaken it by accepting
unknown structural keys as user parameters.
