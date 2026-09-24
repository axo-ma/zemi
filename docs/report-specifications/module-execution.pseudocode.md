# Module Execution Pseudocode

Status: illustrative execution pseudocode.
Date: 2026-09-24.

## Scope

This is execution pseudocode with report writing intentionally omitted. It illustrates parameter sampling, sample execution, evaluation, and result history only. It does not specify where or when reporting methods are called.

ReportWriter construction, MD fragment rendering, report references, and all report-writing calls are excluded for clarity. Their omission does not remove reporting support from the architecture. Reporting contracts are specified separately in [Reporting Architecture](reporting-architecture.spec.md) and [ReportWriter](report-writer.spec.md).

This example covers a module with a configured ModuleOptimizer, in either `start_only` or `optimize` mode. It is illustrative pseudocode, not an implementation or an exact API contract. Error handling and lifecycle details are omitted.

## Pseudocode

```python
param_space = ParamSpace(config=module.params)

optimizer = ModuleOptimizer(
    config=module.optimizer,
    param_space=param_space,
)

trial_dataset = TableDetectionTrialDataset(
    config=module.optimizer.trial_dataset,
)
trial_dataset.load()

history = []

while (param_sample := optimizer.next_param_sample(history)) is not None:
    sample_trial = TableDetectionSampleTrial(
        config=module.optimizer.sample_trial,
        module=module,
        param_sample=param_sample,
        dataset=trial_dataset,
    )

    runs = sample_trial.run()
    metrics, score, feedback = sample_trial.evaluate(runs)

    history.append(SampleTrialResult(
        param_sample=param_sample,
        runs=runs,
        metrics=metrics,
        score=score,
        feedback=feedback,
    ))

    if module.optimizer.mode == "start_only":
        break

if module.optimizer.mode == "optimize":
    best = optimizer.best_param_sample(history)
```

The loop over individual runs belongs inside SampleTrial.run(); it is not expanded here. In `start_only`, only the starting sample is executed. In `optimize`, ModuleOptimizer controls sample selection and selects the best result from history.
