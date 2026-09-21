"""ZEMI Params 0.3 schema, parameter spaces, samplers, and trial loop."""

from __future__ import annotations

import copy
import itertools
import json
import math
import random
import re
from datetime import datetime, timezone
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
_TOP_KEYS = {"system", "component", "arsenals", "playbooks"}
_SYSTEM_KEYS = {"version", "params"}
_COMPONENT_KEYS = {"name", "stop_on_error", "params"}
_ARSENAL_KEYS = {"id", "config_path", "lifecycle", "params"}
_PLAYBOOK_KEYS = {"id", "path", "arsenal", "enabled", "param_space_mode", "params", "sampler"}
_SAMPLER_KEYS = {"strategy", "max_samples", "seed", "blocks", "objective", "sample_trial"}
_TRIAL_KEYS = {"implementation", "path", "params"}
_LEGACY_TRIAL_KEYS = {"dataset", "evaluator", "objective", "run"}
_DATASET_KEYS = {"adapter", "path", "params"}
_EVALUATOR_KEYS = {"adapter", "params"}
_OBJECTIVE_KEYS = {"metric", "direction"}
_STRATEGIES = {"grid", "random", "coordinate", "block_coordinate"}
_PARAM_SPACE_MODES = {"start_only", "sampler"}


def _json_copy(value: Any, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain finite JSON-compatible values: {error}") from error


def _table(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a table")
    return copy.deepcopy(dict(value))


def _closed(table: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ValueError(f"{label} contains unsupported structural keys: {', '.join(sorted(unknown))}")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{label} must match [A-Za-z][A-Za-z0-9_-]*")
    return value


def _path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty path string")
    normalized = value.replace("\\", "/")
    if Path(value).is_absolute() or (normalized.startswith("@") and not normalized.startswith(("@comp/", "@inst/"))):
        raise ValueError(f"{label} must be relative, @comp/..., or @inst/...")
    if ".." in Path(normalized.removeprefix("@comp/").removeprefix("@inst/")).parts:
        raise ValueError(f"{label} must not contain '..'")
    return value


def _params(value: Any, label: str) -> dict[str, Any]:
    result = _table(value, label)
    _json_copy(result, label)
    return result


def _param_space_mode(value: Any, label: str) -> str | dict[str, Any]:
    if isinstance(value, str):
        if value not in _PARAM_SPACE_MODES:
            raise ValueError(f'{label} must be "start_only" or "sampler"')
        return value
    wrapper = _table(value, label)
    if set(wrapper) != {"select"}:
        raise ValueError(f"{label} select wrapper must contain exactly the select key")
    choices = wrapper["select"]
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"{label}.select must be a non-empty array")
    invalid = [choice for choice in choices if choice not in _PARAM_SPACE_MODES]
    if invalid:
        raise ValueError(
            f'{label}.select choices must be "start_only" or "sampler"; got {invalid[0]!r}'
        )
    return wrapper


def validate_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and deep-copy one canonical Params 0.3 document."""
    doc = _table(document, "document")
    _closed(doc, _TOP_KEYS, "document")
    system = _table(doc.get("system"), "system")
    _closed(system, _SYSTEM_KEYS, "system")
    if system.get("version") != "0.3":
        raise ValueError('system.version is required and must be exactly "0.3"')
    system["params"] = _params(system.get("params", {}), "system.params")

    component = _table(doc.get("component"), "component")
    _closed(component, _COMPONENT_KEYS, "component")
    if "name" in component and (not isinstance(component["name"], str) or not component["name"]):
        raise ValueError("component.name must be a non-empty string")
    if not isinstance(component.get("stop_on_error", True), bool):
        raise ValueError("component.stop_on_error must be boolean")
    component["params"] = _params(component.get("params", {}), "component.params")

    arsenals = doc.get("arsenals", [])
    if not isinstance(arsenals, list):
        raise ValueError("arsenals must be an array of tables")
    arsenal_ids: set[str] = set()
    normalized_arsenals = []
    for index, raw in enumerate(arsenals):
        label = f"arsenals[{index}]"
        item = _table(raw, label); _closed(item, _ARSENAL_KEYS, label)
        item_id = _identifier(item.get("id"), f"{label}.id")
        if item_id in arsenal_ids:
            raise ValueError(f"duplicate Arsenal id: {item_id!r}")
        arsenal_ids.add(item_id)
        lifecycle = item.get("lifecycle", "playbook")
        if lifecycle not in {"job", "playbook", "external"}:
            raise ValueError(f"{label}.lifecycle must be job, playbook, or external")
        if "config_path" in item: _path(item["config_path"], f"{label}.config_path")
        if lifecycle == "job" and not item.get("config_path"):
            raise ValueError(f"{label}.config_path is required for lifecycle = 'job'")
        item["lifecycle"] = lifecycle
        item["params"] = _params(item.get("params", {}), f"{label}.params")
        normalized_arsenals.append(item)

    playbooks = doc.get("playbooks")
    if not isinstance(playbooks, list) or not playbooks:
        raise ValueError("playbooks must be a non-empty array of tables")
    playbook_ids: set[str] = set()
    normalized_playbooks = []
    for index, raw in enumerate(playbooks):
        label = f"playbooks[{index}]"
        item = _table(raw, label); _closed(item, _PLAYBOOK_KEYS, label)
        item_id = _identifier(item.get("id"), f"{label}.id")
        if item_id in playbook_ids:
            raise ValueError(f"duplicate playbook id: {item_id!r}")
        playbook_ids.add(item_id)
        _path(item.get("path"), f"{label}.path")
        parent = item.get("arsenal")
        if parent is not None:
            if not isinstance(parent, str) or not parent:
                raise ValueError(f"{label}.arsenal must be a non-empty Arsenal id")
            if parent not in arsenal_ids:
                raise ValueError(f"{label}.arsenal references missing Arsenal {parent!r}")
        if not isinstance(item.get("enabled", True), (bool, Mapping)):
            raise ValueError(f"{label}.enabled must be boolean or a select wrapper")
        mode = item.get("param_space_mode")
        if mode is not None:
            item["param_space_mode"] = _param_space_mode(mode, f"{label}.param_space_mode")
        item["params"] = _params(item.get("params", {}), f"{label}.params")
        dimensions = ParamSpace.from_params(item["params"], f"{label}.params").dimensions
        if "sampler" in item:
            item["sampler"] = _validate_sampler(item["sampler"], f"{label}.sampler")
        literal_mode = item.get("param_space_mode")
        if dimensions:
            names = ", ".join(dimension.name for dimension in dimensions)
            if "sampler" not in item:
                raise ValueError(
                    f"{label}.sampler is required because {label}.params "
                    f"defines variable dimensions: {names}"
                )
            if literal_mode is None:
                raise ValueError(
                    f"{label}.param_space_mode is required because {label}.params "
                    f"defines variable dimensions: {names}"
                )
            if literal_mode == "sampler" and "sample_trial" not in item["sampler"]:
                raise ValueError(
                    f'{label}.sampler.sample_trial is required when {label}.param_space_mode = "sampler"'
                )
            if literal_mode == "sampler" and "objective" not in item["sampler"]:
                raise ValueError(
                    f'{label}.sampler.objective is required when {label}.param_space_mode = "sampler"'
                )
        elif not _may_resolve_dimensions(item["params"]):
            if "sampler" in item:
                raise ValueError(f"{label}.sampler is not allowed because {label}.params are all fixed")
            if literal_mode is not None:
                raise ValueError(f"{label}.param_space_mode is not allowed because {label}.params are all fixed")
        normalized_playbooks.append(item)
    return {"system": system, "component": component, "arsenals": normalized_arsenals, "playbooks": normalized_playbooks}


def _validate_sampler(raw: Any, label: str) -> dict[str, Any]:
    sampler = _table(raw, label); _closed(sampler, _SAMPLER_KEYS, label)
    strategy = sampler.get("strategy")
    if strategy not in _STRATEGIES:
        raise ValueError(f"{label}.strategy must be grid, random, coordinate, or block_coordinate")
    maximum = sampler.get("max_samples")
    if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0):
        raise ValueError(f"{label}.max_samples must be a positive integer")
    if strategy != "grid" and maximum is None:
        raise ValueError(f"{label}.max_samples is required for {strategy}")
    if "seed" in sampler and (not isinstance(sampler["seed"], int) or isinstance(sampler["seed"], bool)):
        raise ValueError(f"{label}.seed must be an integer")
    if "objective" in sampler:
        objective = _table(sampler["objective"], f"{label}.objective")
        _closed(objective, _OBJECTIVE_KEYS, f"{label}.objective")
        if not isinstance(objective.get("metric"), str) or not objective["metric"]:
            raise ValueError(f"{label}.objective.metric must be a non-empty string")
        if objective.get("direction") not in {"maximize", "minimize"}:
            raise ValueError(f"{label}.objective.direction must be maximize or minimize")
        sampler["objective"] = objective
    blocks = sampler.get("blocks")
    if strategy == "block_coordinate":
        sampler["blocks"] = _validate_blocks_shape(blocks, f"{label}.blocks")
    elif blocks is not None:
        raise ValueError(f"{label}.blocks is valid only for block_coordinate")
    if "sample_trial" not in sampler:
        return sampler
    trial = _table(sampler["sample_trial"], f"{label}.sample_trial")
    if set(trial) <= _TRIAL_KEYS:
        _closed(trial, _TRIAL_KEYS, f"{label}.sample_trial")
        implementation = trial.get("implementation")
        if not isinstance(implementation, str) or not implementation:
            raise ValueError(f"{label}.sample_trial.implementation must be a non-empty string")
        if implementation.startswith("@comp/"):
            if not re.fullmatch(r"@comp/[^:]+\.py:[A-Za-z_][A-Za-z0-9_]*", implementation):
                raise ValueError(f"{label}.sample_trial.implementation must be a built-in name or @comp/path.py:ClassOrFactory")
        elif implementation != "table_detection":
            raise ValueError(f"{label}.sample_trial.implementation: unknown built-in {implementation!r}")
        if "path" in trial:
            _path(trial["path"], f"{label}.sample_trial.path")
        trial["params"] = _params(trial.get("params", {}), f"{label}.sample_trial.params")
        sampler["sample_trial"] = trial
        return sampler
    _closed(trial, _LEGACY_TRIAL_KEYS, f"{label}.sample_trial")
    dataset = _table(trial.get("dataset"), f"{label}.sample_trial.dataset")
    _closed(dataset, _DATASET_KEYS, f"{label}.sample_trial.dataset")
    if not isinstance(dataset.get("adapter"), str) or not dataset["adapter"]:
        raise ValueError(f"{label}.sample_trial.dataset.adapter must be a non-empty string")
    if "path" in dataset: _path(dataset["path"], f"{label}.sample_trial.dataset.path")
    dataset["params"] = _params(dataset.get("params", {}), f"{label}.sample_trial.dataset.params")
    evaluator = _table(trial.get("evaluator"), f"{label}.sample_trial.evaluator")
    _closed(evaluator, _EVALUATOR_KEYS, f"{label}.sample_trial.evaluator")
    if not isinstance(evaluator.get("adapter"), str) or not evaluator["adapter"]:
        raise ValueError(f"{label}.sample_trial.evaluator.adapter must be a non-empty string")
    evaluator["params"] = _params(evaluator.get("params", {}), f"{label}.sample_trial.evaluator.params")
    objective = _table(trial.get("objective"), f"{label}.sample_trial.objective")
    _closed(objective, _OBJECTIVE_KEYS, f"{label}.sample_trial.objective")
    if not isinstance(objective.get("metric"), str) or not objective["metric"]:
        raise ValueError(f"{label}.sample_trial.objective.metric must be a non-empty string")
    if objective.get("direction") not in {"maximize", "minimize"}:
        raise ValueError(f"{label}.sample_trial.objective.direction must be maximize or minimize")
    trial.update(dataset=dataset, evaluator=evaluator, objective=objective)
    if "run" in trial:
        adapter = _table(trial["run"], f"{label}.sample_trial.run")
        _closed(adapter, _EVALUATOR_KEYS, f"{label}.sample_trial.run")
        if not isinstance(adapter.get("adapter"), str) or not adapter["adapter"]:
            raise ValueError(f"{label}.sample_trial.run.adapter must be a non-empty string")
        adapter["params"] = _params(adapter.get("params", {}), f"{label}.sample_trial.run.params")
        trial["run"] = adapter
    if "objective" in sampler and sampler["objective"] != objective:
        raise ValueError(f"{label}.objective conflicts with legacy {label}.sample_trial.objective")
    sampler.setdefault("objective", objective)
    sampler["sample_trial"] = {"implementation": "legacy", "params": {}, "_legacy": trial}
    return sampler


def _may_resolve_dimensions(value: Any) -> bool:
    if isinstance(value, Mapping):
        if "ref" in value or "__include__" in value:
            return True
        return any(_may_resolve_dimensions(item) for item in value.values())
    if isinstance(value, list):
        return any(_may_resolve_dimensions(item) for item in value)
    return False


def _validate_blocks_shape(raw: Any, label: str) -> list[list[str]]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or not raw:
        raise ValueError(f"{label} must be a non-empty array of parameter-name arrays")
    blocks: list[list[str]] = []
    seen: set[str] = set()
    for index, raw_block in enumerate(raw):
        block_label = f"{label}[{index}]"
        if isinstance(raw_block, (str, bytes)) or not isinstance(raw_block, Sequence) or not raw_block:
            raise ValueError(f"{block_label} must be a non-empty array of parameter names")
        block = []
        for item_index, name in enumerate(raw_block):
            if not isinstance(name, str) or not name:
                raise ValueError(f"{block_label}[{item_index}] must be a non-empty parameter name")
            if name in seen:
                raise ValueError(f"{label}: parameter {name!r} occurs in more than one block")
            seen.add(name)
            block.append(name)
        blocks.append(block)
    return blocks


def _coordinate_blocks(
    space: "ParamSpace",
    strategy: str,
    blocks: Sequence[Sequence[str]] | None,
    label: str = "sampler.blocks",
) -> tuple[tuple["ParamDimension", ...], ...]:
    dimensions = {dimension.name: dimension for dimension in space.dimensions}
    if strategy == "coordinate":
        if blocks is not None:
            raise ValueError(f"{label} is valid only for block_coordinate")
        return tuple((dimension,) for dimension in space.dimensions)
    if strategy != "block_coordinate":
        if blocks is not None:
            raise ValueError(f"{label} is valid only for block_coordinate")
        return ()
    normalized = _validate_blocks_shape(blocks, label)
    grouped: list[tuple[ParamDimension, ...]] = []
    listed: set[str] = set()
    for block in normalized:
        resolved = []
        for name in block:
            if name in space.fixed:
                raise ValueError(f"{label}: {name!r} is a fixed parameter, not a variable dimension")
            if name not in dimensions:
                raise ValueError(f"{label}: unknown variable dimension {name!r}")
            listed.add(name)
            resolved.append(dimensions[name])
        grouped.append(tuple(resolved))
    grouped.extend((dimension,) for dimension in space.dimensions if dimension.name not in listed)
    return tuple(grouped)


@dataclass(frozen=True)
class ParamDimension:
    name: str
    values: tuple[Any, ...]
    start: Any


@dataclass(frozen=True)
class ParamSample:
    values: Mapping[str, Any]

    def key(self) -> str:
        return json.dumps(self.values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ParamSpace:
    fixed: Mapping[str, Any]
    dimensions: tuple[ParamDimension, ...]

    @classmethod
    def from_params(cls, params: Mapping[str, Any], label: str = "playbook.params") -> "ParamSpace":
        fixed: dict[str, Any] = {}; dimensions = []
        for name, value in params.items():
            if isinstance(value, Mapping) and ("values" in value or "range" in value):
                dimensions.append(_dimension(name, value, f"{label}.{name}"))
            else:
                fixed[name] = _json_copy(value, f"{label}.{name}")
        return cls(fixed, tuple(dimensions))

    @property
    def start(self) -> ParamSample:
        values = copy.deepcopy(dict(self.fixed))
        values.update((dimension.name, copy.deepcopy(dimension.start)) for dimension in self.dimensions)
        return ParamSample(values)

    def sample(self, choices: Sequence[Any]) -> ParamSample:
        values = copy.deepcopy(dict(self.fixed))
        values.update((self.dimensions[index].name, copy.deepcopy(value)) for index, value in enumerate(choices))
        return ParamSample(values)

    def grid(self) -> list[ParamSample]:
        if not self.dimensions: return [self.start]
        samples = [self.start]
        for values in itertools.product(*(dimension.values for dimension in self.dimensions)):
            sample = self.sample(values)
            if sample.key() != samples[0].key(): samples.append(sample)
        return samples


def _dimension(name: str, raw: Mapping[str, Any], label: str) -> ParamDimension:
    if set(raw) not in ({"values", "start"}, {"range", "start"}):
        raise ValueError(f"{label} variable wrapper must contain exactly values/start or range/start")
    start = _json_copy(raw["start"], f"{label}.start")
    if "values" in raw:
        if not isinstance(raw["values"], list) or not raw["values"]:
            raise ValueError(f"{label}.values must be a non-empty array")
        values = tuple(_json_copy(value, f"{label}.values") for value in raw["values"])
    else:
        spec = _table(raw["range"], f"{label}.range")
        if set(spec) != {"min", "max", "step"}:
            raise ValueError(f"{label}.range must contain exactly min, max, and step")
        low, high, step = spec["min"], spec["max"], spec["step"]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (low, high, step)) or step <= 0 or high < low:
            raise ValueError(f"{label}.range requires finite min <= max and step > 0")
        count = int(math.floor((high - low) / step + 1e-12))
        values = tuple(low + index * step for index in range(count + 1))
    keys = [json.dumps(value, sort_keys=True, ensure_ascii=False) for value in values]
    if len(keys) != len(set(keys)): raise ValueError(f"{label} domain values must be unique")
    start_key = json.dumps(start, sort_keys=True, ensure_ascii=False)
    if start_key not in keys: raise ValueError(f"{label}.start must be a member of its domain")
    return ParamDimension(name, values, start)


@dataclass
class SampleResult:
    sample: ParamSample
    runs: list[Any]
    metrics: dict[str, float]
    feedback: Any = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    score: float | None = None
    status: str = "succeeded"
    artifacts: dict[str, Any] = field(default_factory=dict)


# Compatibility name; canonical documentation and custom SampleTrials need not use it.
SampleTrialResult = SampleResult


class ParamSampler:
    """History-driven sampler with no duplicated hidden best-sample state."""

    def __init__(self, space: ParamSpace, strategy: str = "grid", *, max_samples: int | None = None, seed: int | None = None, blocks: Sequence[Sequence[str]] | None = None, objective_metric: str = "objective", direction: str = "maximize", _label: str = "sampler") -> None:
        if strategy not in _STRATEGIES: raise ValueError(f"unsupported sampler strategy: {strategy}")
        self.space = space; self.strategy = strategy; self.max_samples = max_samples
        self.seed = seed; self.objective_metric = objective_metric; self.direction = direction
        if strategy != "grid" and (not isinstance(max_samples, int) or max_samples <= 0): raise ValueError(f"max_samples is required for {strategy}")
        self.blocks = _coordinate_blocks(space, strategy, blocks, f"{_label}.blocks")
        self._candidates = self._build_candidates()

    @property
    def candidates(self) -> tuple[ParamSample, ...]:
        """Return the deterministic candidate plan without exposing mutable state."""
        return tuple(self._candidates)

    def _build_candidates(self) -> list[ParamSample]:
        if self.strategy == "grid": candidates = self.space.grid()
        elif self.strategy == "random":
            grid = self.space.grid()
            tail = grid[1:]; random.Random(self.seed).shuffle(tail); candidates = [grid[0], *tail]
        else:
            start = self.space.start; candidates = [start]
            for block in self.blocks:
                domains = [dimension.values for dimension in block]
                for values in itertools.product(*domains):
                    sample_values = copy.deepcopy(dict(start.values))
                    sample_values.update((block[index].name, copy.deepcopy(value)) for index, value in enumerate(values))
                    sample = ParamSample(sample_values)
                    if sample.key() not in {candidate.key() for candidate in candidates}: candidates.append(sample)
        return candidates[:self.max_samples] if self.max_samples is not None else candidates

    def next_sample(self, history: Sequence[SampleTrialResult]) -> ParamSample | None:
        if self.max_samples is not None and len(history) >= self.max_samples:
            return None
        observed = {result.sample.key() for result in history}
        if self.strategy in {"coordinate", "block_coordinate"}:
            if not history:
                return self.space.start
            best = self.best_result(history)
            anchor = best.sample if best else self.space.start
            for block in self.blocks:
                for values in itertools.product(*(d.values for d in block)):
                    candidate = copy.deepcopy(dict(anchor.values))
                    candidate.update((d.name, value) for d, value in zip(block, values))
                    sample = ParamSample(candidate)
                    if sample.key() not in observed:
                        return sample
            return None
        return next((sample for sample in self._candidates if sample.key() not in observed), None)

    def best_result(self, history: Sequence[SampleTrialResult]) -> SampleTrialResult | None:
        def score(item):
            return item.score if item.score is not None else item.metrics.get(self.objective_metric)
        valid = [item for item in history if item.error is None and item.status == "succeeded"
                 and not isinstance(score(item), bool) and isinstance(score(item), (int, float))
                 and math.isfinite(score(item))]
        if not valid:
            return None
        return (max if self.direction == "maximize" else min)(valid, key=score)

    def best_sample(self, history: Sequence[SampleTrialResult]) -> ParamSample | None:
        best = self.best_result(history)
        return best.sample if best is not None else None

    # Compatibility aliases for the pre-SampleTrial public surface.
    def propose(self, history: Sequence[SampleTrialResult]) -> ParamSample | None:
        return self.next_sample(history)

    def observe(self, history: Sequence[SampleTrialResult], result: SampleTrialResult) -> None:
        if result.sample.key() in {item.sample.key() for item in history[:-1]}:
            raise ValueError("sampler proposed a duplicate ParamSample")


@dataclass
class PlaybookTrialResult:
    history: list[SampleTrialResult] = field(default_factory=list)

    def best(self, metric: str, direction: str) -> SampleTrialResult | None:
        valid = [item for item in self.history if item.error is None and metric in item.metrics
                 and not isinstance(item.metrics[metric], bool)
                 and isinstance(item.metrics[metric], (int, float)) and math.isfinite(item.metrics[metric])]
        if not valid: return None
        return (max if direction == "maximize" else min)(valid, key=lambda item: item.metrics[metric])


def run_playbook_trial(*, sampler: ParamSampler, dataset: Iterable[Any], run: Callable[[ParamSample, Any], Any], evaluator: Callable[[ParamSample, Sequence[Any]], Mapping[str, Any] | tuple[Mapping[str, Any], Any]], metric: str, direction: str, on_sample=None) -> PlaybookTrialResult:
    """Execute the required propose -> runs -> evaluate -> observe outer loop."""
    result = PlaybookTrialResult()
    items = list(dataset)
    while (sample := sampler.next_sample(result.history)) is not None:
        if sample.key() in {trial.sample.key() for trial in result.history}:
            raise ValueError("sampler proposed a duplicate ParamSample")
        started = datetime.now(timezone.utc).isoformat()
        runs = []
        for item in items:
            try:
                runs.append(run(sample, item))
            except Exception as failure:
                runs.append({"item": item, "prediction": None, "error": str(failure)})
        metrics: dict[str, float] = {}
        feedback = error = None
        try:
            evaluated = evaluator(sample, runs)
            metrics_raw, feedback = evaluated if isinstance(evaluated, tuple) else (evaluated, None)
            if not isinstance(metrics_raw, Mapping): raise ValueError("evaluator must return a metric map or (metric map, feedback)")
            for name, value in metrics_raw.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"evaluator metric {name!r} must be a finite number")
                metrics[str(name)] = float(value)
            if metric not in metrics: raise ValueError(f"evaluator result does not contain objective metric {metric!r}")
            feedback = _json_copy(feedback, "evaluator feedback")
        except Exception as failure:
            error = str(failure)
            feedback = None
        score = metrics.get(metric) if error is None else None
        trial = SampleTrialResult(sample, runs, metrics, feedback, error, started,
                                  datetime.now(timezone.utc).isoformat(), score,
                                  "failed" if error else "succeeded")
        result.history.append(trial); sampler.observe(result.history, trial)
        if on_sample is not None:
            on_sample(trial, result)
    return result


__all__ = ["ParamDimension", "ParamSample", "ParamSampler", "ParamSpace", "PlaybookTrialResult", "SampleResult", "SampleTrialResult", "run_playbook_trial", "validate_document"]
