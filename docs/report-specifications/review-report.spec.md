# Review Report

ZEMI automatically creates a Review Report for each enabled optimized Module,
including `start_only`, during `ZemiComponent.run()`. The job only selects a
parameter TOML, runs the component and closes it. There is no manual review
configuration API, settings table or source/repository list in the job or TOML.

One `<module_id>.review.md` report is linked from its Module Report. It refreshes
through the standard lifecycle, including partial and failed Runs. ReportWriter
owns filenames, navigation, redaction and atomic writes.

## Authoritative inputs

All inputs are collected from resolved configuration:

- selected `params.toml` and configured playbook;
- named templates and examples from `encoding_prompt.prompt_file`;
- encoder sources from `encoding_prompt.encoder`;
- dataset manifest from `optimizer.trial_dataset.path`;
- SampleTrial implementation from `optimizer.sample_trial.type`;
- configured Arsenal file and model/runtime parameters;
- executing job, when it is a real file inside the component.

Prompts and examples vary through paired `encoding_prompt` parameter choices.
No report-only prompt metadata or generated prompt descriptions are required.

## Saved snapshot

Before Arsenal starts, ZEMI writes `<module_id>.review.json` with the selected
source text, complete templates, optimizer/runtime configuration and actual
Git remote/HEAD/dirty state. Repository provenance includes the component,
its bundled library and repositories owning workbook inputs. Source paths
outside the component use `@inst/`. Workbook bytes are represented by SHA-256
checksums. Known secrets are redacted. No missing commits are guessed.

The snapshot is authoritative for what was collected at launch. A dirty
checkout needs its saved source snapshot in addition to the recorded commit.
Imported dependencies are reproduced through the recorded repositories and
configured environment; they are not recursively copied into the snapshot.

## Markdown layout

1. `Run configuration`: Setting / Value table with actual run status, dates,
   job/params/playbook, model/runtime, optimizer, dataset/SampleTrial, item,
   Sample and Run counts, Git commits and dirty state.
2. `Reproduction`: clone, exact checkout, submodule initialization, component
   environment initialization and configured Python job command. If launched
   programmatically without a component job file, show a direct Python command
   using the selected parameter file.
3. `Results`: Sample / parameters, Score, Mean item tokens, Mean prompt tokens,
   Evaluator errors. One row per started Sample. Score is the SampleTrial score.
   Means use available numeric outputs; missing values show `—`. Float values
   use three decimals. Counts and labels come from the actual results.
4. `Prompts and examples`: full templates loaded from configured bindings.
5. Link to the launch-time JSON snapshot.

No comparison-run score, change column or generated narrative analysis.
