# Review Report

Configure before `component.run()` using
`zemi.review.configure_review(component, entrypoint, settings=..., prompts=...,
sources=..., repositories=...)`. The standard helper configures every optimized
Module, including `start_only`; plain Modules are skipped. Use `module_id` to
select one optimized Module. The component template calls this helper by default.
The lower-level `component.reporting.configure_review(...)` remains available.

The helper collects model identifier, runtime, context, threads and reasoning
from the matching model in the supplied `arsenal_config_path` managed llama
configuration, and temperature/max_tokens from Module parameters. It snapshots
that configuration automatically. Domain-specific settings and prompt templates
are supplied by the component; supplied settings override collected settings.
No Arsenal activation or model calls occur during review setup.
Path arguments use `@comp/` or `@inst/`. No new Params structural sections or
optimization dimensions are introduced.

One `<module_id>.review.md` report is generated for each configured optimized
module, and linked from its Module Report. It is refreshed through the normal
report lifecycle, including partial or failed runs. ReportWriter owns its
filename, navigation, secret redaction and atomic writes.

## Saved inputs

Before execution, write `<module_id>.review.json`: job and Python paths,
parameter file, playbook, optimizer configuration, supplied settings,
full prompt templates (including examples), source file contents and Git
remote/HEAD/dirty state for the component, library and supplied repositories.
Source snapshots include the entrypoint, params and playbook automatically.
Additional sources should include prompt specifications, encoding modules,
dataset manifest and model configuration. Known secret values are redacted.
Do not reconstruct missing Git commits from dates.

## Markdown layout

1. `Run configuration`: Setting / Value table, including run ID, status,
   timestamps, job, params, playbook, model/runtime settings, actual item,
   sample and run counts, repository commits and dirty state at launch.
2. `Reproduction`: PowerShell clone, exact checkout, submodule initialization,
   component environment initialization and selected Python job command.
   Run these commands from a ZEMI Instance root. Required environment/model/runtime
   settings remain explicit. A dirty checkout requires the saved source snapshot.
3. `Results`: Sample / parameters / Score / Mean item tokens /
   Mean prompt tokens / Evaluator errors. One row per started sample.
   Score is the SampleTrial score. Each token mean uses available numeric
   run outputs. No values produces `—`. Display floats to three decimals.
4. `Prompts and examples`: one full saved template per supplied format.
5. Link to the launch-time JSON source snapshot.

No comparison-run score, change column or generated narrative analysis.

## Example

```python
from zemi.review import configure_review

configure_review(
    component, "@comp/job.py", module_id="detect-tables",
    settings={"Output format": '{"ranges":[...]}'},
    prompts={"cell_all": prompt_template},
    sources=["@comp/encoding.py", "@comp/prompts.md"],
    repositories=["@inst/zemi_tests"],
)
component.run()
```
