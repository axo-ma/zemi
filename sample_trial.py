"""Public SampleTrial extension contract and built-in implementations."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from datetime import datetime, timezone
from types import SimpleNamespace
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .dataset import RunContext, resolve_adapter, table_dataset, table_evaluator, zemi_path
from .params import ParamSample, SampleTrialResult


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class SampleTrial:
    """One sample-on-dataset execution policy; the sole public experiment adapter."""

    def __init__(self, config: Mapping[str, Any], *, execute: Callable[..., dict[str, Any]]) -> None:
        self.config = dict(config)
        self.params = dict(self.config.get("params", {}))
        self.path = self.config.get("path")
        self.execute = execute

    def load_dataset(self) -> list[Any]:
        raise NotImplementedError

    def run(self, *, playbook: Any, sample: ParamSample, dataset: Sequence[Any]) -> list[dict[str, Any]]:
        context = RunContext()
        try:
            return [self.execute(playbook=playbook, sample=sample, item=item, context=context) for item in dataset]
        finally:
            context.close()

    def evaluate(self, *, runs: Sequence[dict[str, Any]]) -> tuple[Mapping[str, Any], Any]:
        raise NotImplementedError

    def result(self, *, sample: ParamSample, runs: Sequence[dict[str, Any]], score: Any,
               metrics: Mapping[str, Any], feedback: Any = None, error: str | None = None,
               started_at: str | None = None, finished_at: str | None = None) -> SampleTrialResult:
        if error is None:
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError("SampleTrial score must be one finite number")
            if not isinstance(metrics, Mapping):
                raise ValueError("SampleTrial metrics must be a mapping")
            normalized = {}
            for name, value in metrics.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"SampleTrial metric {name!r} must be a finite number")
                normalized[str(name)] = float(value)
            json.dumps(feedback, ensure_ascii=False, allow_nan=False)
        else:
            normalized = {}
            score = None
            feedback = None
        artifacts = {}
        for run in runs:
            if isinstance(run, Mapping) and run.get("artifacts"):
                artifacts[str(run.get("playbook_run_id", len(artifacts)))] = run["artifacts"]
        return SampleTrialResult(
            sample=sample, runs=list(runs), metrics=normalized, feedback=feedback,
            error=error, started_at=started_at or _timestamp(),
            finished_at=finished_at or _timestamp(), score=float(score) if score is not None else None,
            status="failed" if error else "succeeded", artifacts=artifacts,
        )

    def render_report(self, history: Sequence[SampleTrialResult], sampler_config: Mapping[str, Any],
                      best_sample: ParamSample | None) -> str:
        return ""


class TableDetectionSampleTrial(SampleTrial):
    """Built-in exact table-boundary SampleTrial; score defaults to aggregate F1."""

    def load_dataset(self) -> list[Any]:
        return list(table_dataset(path=self.path, params=self.params))

    def evaluate(self, *, runs: Sequence[dict[str, Any]]) -> tuple[Mapping[str, Any], Any]:
        metrics, feedback = table_evaluator(SimpleNamespace(runs=runs), params=self.params)
        return metrics, feedback

    def render_report(self, history: Sequence[SampleTrialResult], sampler_config: Mapping[str, Any],
                      best_sample: ParamSample | None) -> str:
        lines = ["### Table detection", ""]
        for ordinal, result in enumerate(history, 1):
            lines.extend((f"#### Sample {ordinal}", "",
                          "| Worksheet | Ground truth | Prediction | TP | FP | FN | Precision | Recall | F1 | Diagnostic |",
                          "|---|---|---|---:|---:|---:|---:|---:|---:|---|"))
            details = result.feedback.get("items", []) if isinstance(result.feedback, Mapping) else []
            for item in details:
                values = [item.get("input"), item.get("ground_truth"), item.get("prediction"),
                          *[item.get(key) for key in ("tp", "fp", "fn", "precision", "recall", "f1")],
                          item.get("error")]
                cells = [json.dumps(value, ensure_ascii=False, sort_keys=True).replace("|", "\\|") for value in values]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
        return "\n".join(lines)


class LegacySampleTrial(SampleTrial):
    """Explicit compatibility bridge for pre-unified SampleTrial documents."""

    def __init__(self, config: Mapping[str, Any], *, execute: Callable[..., dict[str, Any]]) -> None:
        super().__init__(config, execute=execute)
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

    def run(self, *, playbook: Any, sample: ParamSample, dataset: Sequence[Any]) -> list[dict[str, Any]]:
        context = RunContext()
        records = []
        try:
            for item in dataset:
                records.append(self.execute(playbook=playbook, sample=sample, item=item, context=context,
                    runner=self.runner, runner_params=self.run_config.get("params", {})))
            return records
        finally:
            context.close()

    def evaluate(self, *, runs: Sequence[dict[str, Any]]) -> tuple[Mapping[str, Any], Any]:
        evaluated = self.evaluator(SimpleNamespace(runs=runs), params=self.evaluator_config.get("params", {}))
        metrics, feedback = evaluated if isinstance(evaluated, tuple) else (evaluated, None)
        return metrics, feedback


def _load_custom(reference: str) -> Any:
    filename, name = reference.rsplit(":", 1)
    file = zemi_path(filename)
    if not file.is_file():
        raise FileNotFoundError(f"SampleTrial implementation not found: {file}")
    module_name = f"_zemi_sample_trial_{hashlib.sha256(str(file).encode()).hexdigest()}"
    spec = importlib.util.spec_from_file_location(module_name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    implementation = getattr(module, name, None)
    if not callable(implementation):
        raise ValueError(f"SampleTrial implementation {reference!r} is not callable")
    return implementation


def resolve_sample_trial(config: Mapping[str, Any], *, execute: Callable[..., dict[str, Any]]) -> SampleTrial:
    implementation = config["implementation"]
    if implementation == "table_detection":
        instance = TableDetectionSampleTrial(config, execute=execute)
    elif implementation == "legacy":
        instance = LegacySampleTrial(config, execute=execute)
    else:
        factory = _load_custom(implementation)
        instance = factory(config=config, execute=execute)
    required = ("load_dataset", "run", "evaluate", "result", "render_report")
    missing = [name for name in required if not callable(getattr(instance, name, None))]
    if missing:
        raise ValueError(f"SampleTrial implementation is missing methods: {', '.join(missing)}")
    return instance


__all__ = ["SampleTrial", "TableDetectionSampleTrial", "resolve_sample_trial"]
