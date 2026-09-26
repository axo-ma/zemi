"""Public SampleTrial extension contract and built-in implementations."""
from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .dataset import RunContext, TableDetectionTrialDataset, TrialDataset, resolve_adapter, table_evaluator, zemi_path
from .params import ParamSample


class SampleTrial:
    """One sample-on-dataset execution policy; the sole public experiment adapter."""

    def __init__(self, config: Mapping[str, Any], *, module: Any = None,
                 param_sample: ParamSample | None = None, dataset: Any = None,
                 execute: Callable[..., dict[str, Any]] | None = None) -> None:
        self.config = dict(config)
        self.params = dict(self.config.get("params", {}))
        self.module = module
        self.param_sample = param_sample
        self.dataset = dataset
        self.execute = execute

    def run(self) -> list[dict[str, Any]]:
        context = RunContext()
        try:
            items = self.dataset.items if isinstance(self.dataset, (TrialDataset, TableDetectionTrialDataset)) else self.dataset
            return [self.execute(playbook=self.module, sample=self.param_sample, item=item, context=context) for item in items]
        finally:
            context.close()

    def evaluate(self, runs: Sequence[dict[str, Any]]) -> tuple[Mapping[str, Any], float, Any]:
        raise NotImplementedError

    def render_report(self, runs, metrics, score, feedback) -> str:
        from .reporting import DefaultReportRenderer
        return DefaultReportRenderer().render_sample_trial(sample_trial=self, runs=runs,
            metrics=metrics, score=score, feedback=feedback)

    def render_run_report(self, run, *, writer=None, module_id=None, sample_id=None) -> str:
        from .reporting import DefaultReportRenderer
        return DefaultReportRenderer().render_run_report(run=run, writer=writer,
            module_id=module_id, sample_id=sample_id)


class TableDetectionSampleTrial(SampleTrial):
    """Built-in exact table-boundary SampleTrial; score defaults to aggregate F1."""

    def evaluate(self, runs: Sequence[dict[str, Any]]) -> tuple[Mapping[str, Any], float, Any]:
        for run in runs:
            prediction = run.get("prediction")
            run["comparison_prediction"] = prediction.get("ranges") if isinstance(prediction, Mapping) else None
        metrics, feedback = table_evaluator(SimpleNamespace(runs=runs), params=self.params)
        return metrics, metrics["f1"], feedback


class LegacySampleTrial(SampleTrial):
    """Explicit compatibility bridge for pre-unified SampleTrial documents."""

    def __init__(self, config: Mapping[str, Any], *, module: Any = None,
                 param_sample: ParamSample | None = None, dataset: Any = None,
                 execute: Callable[..., dict[str, Any]] | None = None) -> None:
        super().__init__(config, module=module, param_sample=param_sample, dataset=dataset, execute=execute)
        self.legacy = dict(config["_legacy"])
        self.dataset_config = self.legacy["dataset"]
        self.evaluator_config = self.legacy["evaluator"]
        self.run_config = self.legacy.get("run", {"adapter": "notebook", "params": {}})
        self.objective = self.legacy["objective"]
        self.loader = resolve_adapter("dataset", self.dataset_config["adapter"])
        self.evaluator = resolve_adapter("evaluator", self.evaluator_config["adapter"])
        self.runner = resolve_adapter("run", self.run_config.get("adapter", "notebook"))

    def load_dataset(self) -> list[Any]:
        return list(self.loader(path=self.dataset_config.get("path"), params=self.dataset_config.get("params", {})))

    def run(self) -> list[dict[str, Any]]:
        context = RunContext()
        records = []
        try:
            for item in self.dataset.items:
                records.append(self.execute(playbook=self.module, sample=self.param_sample, item=item, context=context,
                    runner=self.runner, runner_params=self.run_config.get("params", {})))
            return records
        finally:
            context.close()

    def evaluate(self, runs: Sequence[dict[str, Any]]) -> tuple[Mapping[str, Any], float, Any]:
        evaluated = self.evaluator(SimpleNamespace(runs=runs), params=self.evaluator_config.get("params", {}))
        metrics, feedback = evaluated if isinstance(evaluated, tuple) else (evaluated, None)
        metric = self.objective["metric"]
        score = metrics[metric]
        if self.objective.get("direction") == "minimize":
            score = -score
        return metrics, score, feedback


def _load_custom(reference: str) -> Any:
    filename, name = reference.rsplit(":", 1)
    if filename == "@comp/zemi/sample_trial.py":
        implementation = globals().get(name)
        if not isinstance(implementation, type):
            raise ValueError(f"SampleTrial type {reference!r} is not a class")
        return implementation
    file = zemi_path(filename)
    if not file.is_file():
        raise FileNotFoundError(f"SampleTrial implementation not found: {file}")
    module_name = f"_zemi_sample_trial_{abs(hash(str(file.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    implementation = getattr(module, name, None)
    if not isinstance(implementation, type):
        raise ValueError(f"SampleTrial type {reference!r} is not a class")
    return implementation


def resolve_sample_trial(config: Mapping[str, Any], *, module: Any = None,
                         param_sample: ParamSample | None = None, dataset: Any = None,
                         execute: Callable[..., dict[str, Any]] | None = None) -> SampleTrial:
    implementation = config.get("type")
    if implementation == "legacy":
        instance = LegacySampleTrial(config, module=module, param_sample=param_sample,
                                     dataset=dataset, execute=execute)
    else:
        factory = _load_custom(implementation)
        if not issubclass(factory, SampleTrial):
            raise ValueError(f"SampleTrial type {implementation!r} must inherit SampleTrial")
        instance = factory(config=config, module=module, param_sample=param_sample,
                           dataset=dataset, execute=execute)
    required = ("run", "evaluate", "render_report")
    missing = [name for name in required if not callable(getattr(instance, name, None))]
    if missing:
        raise ValueError(f"SampleTrial implementation is missing methods: {', '.join(missing)}")
    return instance


__all__ = ["SampleTrial", "TableDetectionSampleTrial", "resolve_sample_trial"]
