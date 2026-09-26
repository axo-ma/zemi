# Reproduction Report Specification

Location: `<module_id>.reproduction.md` in the run root, with launch-time
source snapshot `<module_id>.reproduction.json`. Module artifacts link to it.
ZemiComponent.run captures this information automatically before Arsenal starts.
Jobs contain no manual settings, sources or review setup.

Markdown sections: Run configuration, Reproduction, Source snapshot.
There is no Results or Prompts and examples section. Sample metrics and mean
tokens belong in the Module Report; selected prompts belong in Sample Reports.

Configuration includes execution status/times, job and params paths, playbook,
model/runtime information, optimizer policy, dataset/sample/run counts, actual
repository commits and dirty-at-launch flags. Reproduction commands preserve
clone URLs, commits, submodule initialization, Python setup and entry point.
The report refreshes status and counts without calling a language model.

The JSON snapshot retains full prompts, source text, selected configuration,
input checksums and Git provenance, even though prompts are not repeated in
this Markdown report. Dirty checkout reproduction requires these snapshots.

Existing internal review method names remain compatibility entry points;
generated report titles, artifact names, links and filenames use Reproduction.
