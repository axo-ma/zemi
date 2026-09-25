# Module Runs Report Specification

Status: accepted for implementation.

The Module Runs Report contains the module-wide run listing formerly embedded in Module Report. Proposed location: `<module_id>.runs.md`. It is distinct from an individual Run Report.

This report resides directly in the job run directory alongside Module Report and Job Report. Identify the module and Job run ID, and link back to `<module_id>.md` and `index.md`.

## Runs

List all started runs across samples in execution order using Run, Status, Outputs, and Duration columns. Each Run links to its individual Run Report under `runs/`. The Sample number in each section heading links to its Sample Report under `samples/`; show that sample's parameter names and values below the heading. Resolve all paths from actual report references.

The Outputs header has a second line listing output names selected by playbooks through `output_params(..., report=[...])`, separated by ` / `. Each row shows the selected values in the same order, using `—` for a missing value. Output names and values are dynamic; use the union across runs in stable order. All output parameters remain in the detailed Run Report. Duration is total run time; a playbook may publish a separate `lm_time` output to measure only its model request, displayed as `LM Time`.

If no runs have started, show an explicit empty state. The Job Report and Module Report Runs counters link here. Create this report before emitting those links.

Provide a stable section anchor for every started sample, including samples with no started runs. Group runs by sample in sample execution order and preserve run execution order within each group. Module Report sample-row Runs counters link to these sections, for example `<module_id>.runs.md#sample-002`. Use actual sample identities with a stable unique anchor mapping; show ordinary sample sequence numbers in visible navigation, not circled digits. Keep full sample identifiers available for identification.

The Module runs summary fragment is rendered by `render_module_runs_summary` and written through `write_module_runs_summary(module_id, md_fragment)`. The Module executor supplies it. No individual run listing is embedded in Module Report.
