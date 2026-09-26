"""ZEMI Params 0.6 schema, parameter spaces, optimizers, and trial loop."""

from __future__ import annotations

import copy
import itertools
import json
import math
import random
import re
import warnings
from datetime import datetime, timezone
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
_TOP_KEYS = {"system", "component", "arsenals", "modules"}
_SYSTEM_KEYS = {"version", "params"}
_COMPONENT_KEYS = {"name", "stop_on_error", "params"}
_ARSENAL_KEYS = {"id", "config_path", "lifecycle", "params"}
_MODULE_KEYS = {"id", "kind", "path", "arsenal", "enabled", "params", "optimizer"}
_OPTIMIZER_KEYS = {"mode", "strategy", "max_trials", "max_samples", "seed", "blocks", "sample_trial", "trial_dataset", "reuse_kernel"}
_TRIAL_KEYS = {"type", "dataset", "params"}
_TRIAL_DATASET_KEYS = {"path", "type"}
_LEGACY_TRIAL_KEYS = {"dataset", "evaluator", "objective", "run"}
_DATASET_KEYS = {"adapter", "path", "params"}
_EVALUATOR_KEYS = {"adapter", "params"}
_OBJECTIVE_KEYS = {"metric", "direction"}
_STRATEGIES = {"grid", "random", "coordinate", "block_coordinate"}


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


def validate_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and deep-copy one canonical Params 0.6 document."""
    doc = _table(document, "document")
    version = doc.get("system", {}).get("version") if isinstance(doc.get("system"), Mapping) else None
    if version == "0.3":
        doc = _migrate_03(doc)
        warnings.warn("Params 0.3 is deprecated; migrated in memory to Params 0.6", DeprecationWarning, stacklevel=2)
    elif version == "0.5":
        doc = _migrate_05(doc)
        warnings.warn("Params 0.5 [[playbooks]] is deprecated; migrated in memory to Params 0.6 [[modules]]", DeprecationWarning, stacklevel=2)
    _closed(doc, _TOP_KEYS, "document")
    system = _table(doc.get("system"), "system")
    _closed(system, _SYSTEM_KEYS, "system")
    if system.get("version") != "0.6":
        raise ValueError('system.version is required and must be exactly "0.6"')
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

    modules = doc.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ValueError("modules must be a non-empty array of tables")
    module_ids: set[str] = set()
    normalized_modules = []
    for index, raw in enumerate(modules):
        label = f"modules[{index}]"
        item = _table(raw, label); _closed(item, _MODULE_KEYS, label)
        item_id = _identifier(item.get("id"), f"{label}.id")
        if item_id in module_ids:
            raise ValueError(f"duplicate Module id: {item_id!r}")
        module_ids.add(item_id)
        kind = item.get("kind")
        if kind != "playbook":
            raise ValueError(f"{label}.kind is required and currently only 'playbook' is supported")
        _path(item.get("path"), f"{label}.path")
        parent = item.get("arsenal")
        if parent is not None:
            if not isinstance(parent, str) or not parent:
                raise ValueError(f"{label}.arsenal must be a non-empty Arsenal id")
            if parent not in arsenal_ids:
                raise ValueError(f"{label}.arsenal references missing Arsenal {parent!r}")
        if not isinstance(item.get("enabled", True), (bool, Mapping)):
            raise ValueError(f"{label}.enabled must be boolean or a select wrapper")
        item["params"] = _params(item.get("params", {}), f"{label}.params")
        dimensions = ParamSpace(config=item["params"], label=f"{label}.params").dimensions
        if "optimizer" in item:
            item["optimizer"] = _validate_optimizer(item["optimizer"], f"{label}.optimizer")
        if dimensions:
            names = ", ".join(dimension.name for dimension in dimensions)
            if "optimizer" not in item:
                raise ValueError(
                    f"{label}.optimizer is required because {label}.params "
                    f"defines variable dimensions: {names}"
                )
            if "sample_trial" not in item["optimizer"]:
                raise ValueError(f"{label}.optimizer.sample_trial is required for variable parameters")
            if "trial_dataset" not in item["optimizer"]:
                raise ValueError(f"{label}.optimizer.trial_dataset is required for variable parameters")
        elif not _may_resolve_dimensions(item["params"]):
            if "optimizer" in item:
                raise ValueError(f"{label}.optimizer is not allowed because {label}.params are all fixed")
        normalized_modules.append(item)
    return {"system": system, "component": component, "arsenals": normalized_arsenals, "modules": normalized_modules}


def _validate_optimizer(raw: Any, label: str) -> dict[str, Any]:
    optimizer = _table(raw, label); _closed(optimizer, _OPTIMIZER_KEYS, label)
    mode = optimizer.get("mode", "optimize")
    if not ((isinstance(mode, str) and mode in {"optimize", "start_only"})
            or (isinstance(mode, Mapping) and set(mode) == {"select"})):
        raise ValueError(f"{label}.mode must be optimize, start_only, or a select wrapper")
    optimizer["mode"] = copy.deepcopy(mode)
    reuse_kernel = optimizer.get("reuse_kernel", True)
    if not isinstance(reuse_kernel, bool):
        raise ValueError(f"{label}.reuse_kernel must be boolean")
    optimizer["reuse_kernel"] = reuse_kernel
    strategy = optimizer.get("strategy")
    if strategy not in _STRATEGIES:
        raise ValueError(f"{label}.strategy must be grid, random, coordinate, or block_coordinate")
    if "max_trials" in optimizer and "max_samples" in optimizer:
        raise ValueError(f"{label} must not define both max_trials and deprecated max_samples")
    maximum = optimizer.get("max_trials", optimizer.get("max_samples"))
    if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0):
        raise ValueError(f"{label}.max_trials must be a positive integer")
    if strategy != "grid" and maximum is None:
        raise ValueError(f"{label}.max_trials is required for {strategy}")
    if "max_samples" in optimizer:
        warnings.warn("optimizer.max_samples is deprecated; use max_trials", DeprecationWarning, stacklevel=3)
        optimizer["max_trials"] = optimizer.pop("max_samples")
    if "seed" in optimizer and (not isinstance(optimizer["seed"], int) or isinstance(optimizer["seed"], bool)):
        raise ValueError(f"{label}.seed must be an integer")
    blocks = optimizer.get("blocks")
    if strategy == "block_coordinate":
        optimizer["blocks"] = _validate_blocks_shape(blocks, f"{label}.blocks")
    elif blocks is not None:
        raise ValueError(f"{label}.blocks is valid only for block_coordinate")
    if isinstance(optimizer.get("sample_trial"), Mapping) and "dataset" in optimizer["sample_trial"]:
        if "trial_dataset" in optimizer:
            raise ValueError(f"{label} must not define both sample_trial.dataset and trial_dataset")
        optimizer["trial_dataset"] = {"path": optimizer["sample_trial"].pop("dataset")}
        warnings.warn("sample_trial.dataset is deprecated; use optimizer.trial_dataset.path", DeprecationWarning, stacklevel=3)
    if "trial_dataset" in optimizer:
        dataset = _table(optimizer["trial_dataset"], f"{label}.trial_dataset")
        _closed(dataset, _TRIAL_DATASET_KEYS, f"{label}.trial_dataset")
        dataset["path"] = _path(dataset.get("path"), f"{label}.trial_dataset.path")
        if "type" in dataset and (not isinstance(dataset["type"], str) or not re.fullmatch(r"@comp/[^:]+\.py:[A-Za-z_][A-Za-z0-9_]*", dataset["type"])):
            raise ValueError(f"{label}.trial_dataset.type must be @comp/path.py:ClassName")
        optimizer["trial_dataset"] = dataset
    if "sample_trial" not in optimizer:
        return optimizer
    trial = _table(optimizer["sample_trial"], f"{label}.sample_trial")
    _closed(trial, _TRIAL_KEYS, f"{label}.sample_trial")
    trial_type = trial.get("type")
    if not isinstance(trial_type, str) or not re.fullmatch(r"@comp/[^:]+\.py:[A-Za-z_][A-Za-z0-9_]*", trial_type):
        raise ValueError(f"{label}.sample_trial.type must be @comp/path.py:ClassName")
    trial["params"] = _params(trial.get("params", {}), f"{label}.sample_trial.params")
    optimizer["sample_trial"] = trial
    return optimizer


def _migrate_03(doc: dict[str, Any]) -> dict[str, Any]:
    """Translate the final 0.3 surface to 0.6 without keeping it canonical."""
    migrated = copy.deepcopy(doc)
    migrated["system"]["version"] = "0.6"
    for playbook in migrated.get("playbooks", []):
        mode = playbook.pop("param_space_mode", None)
        sampler = playbook.pop("sampler", None)
        if sampler is None:
            continue
        if mode == "start_only":
            # A deliberate one-off 0.3 run becomes fixed parameters after resolution.
            for name, value in list(playbook.get("params", {}).items()):
                if isinstance(value, Mapping) and "start" in value:
                    playbook["params"][name] = copy.deepcopy(value["start"])
            continue
        optimizer = {key: copy.deepcopy(value) for key, value in sampler.items()
                     if key in {"strategy", "max_samples", "seed", "blocks"}}
        old_trial = sampler.get("sample_trial")
        if isinstance(old_trial, Mapping):
            implementation = old_trial.get("implementation")
            if implementation == "table_detection":
                trial_type = "@comp/zemi/sample_trial.py:TableDetectionSampleTrial"
            elif isinstance(implementation, str) and implementation.startswith("@comp/"):
                trial_type = implementation
            else:
                raise ValueError("Params 0.3 legacy adapter-style SampleTrial cannot be migrated automatically")
            optimizer["sample_trial"] = {
                "type": trial_type,
                "params": copy.deepcopy(old_trial.get("params", {})),
            }
            optimizer["trial_dataset"] = {"path": old_trial.get("path")}
        playbook["optimizer"] = optimizer
    migrated["modules"] = migrated.pop("playbooks", [])
    for module in migrated["modules"]:
        module["kind"] = "playbook"
    return migrated


def _migrate_05(doc: dict[str, Any]) -> dict[str, Any]:
    migrated = copy.deepcopy(doc)
    migrated["system"]["version"] = "0.6"
    migrated["modules"] = migrated.pop("playbooks", [])
    for module in migrated["modules"]:
        module["kind"] = "playbook"
    return migrated


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
    label: str = "optimizer.blocks",
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


@dataclass(frozen=True, init=False)
class ParamSpace:
    fixed: Mapping[str, Any]
    dimensions: tuple[ParamDimension, ...]

    def __init__(self, config: Mapping[str, Any], label: str = "module.params") -> None:
        fixed: dict[str, Any] = {}; dimensions = []
        for name, value in config.items():
            if isinstance(value, Mapping) and ("values" in value or "range" in value):
                dimensions.append(_dimension(name, value, f"{label}.{name}"))
            else:
                fixed[name] = _json_copy(value, f"{label}.{name}")
        object.__setattr__(self, "fixed", fixed)
        object.__setattr__(self, "dimensions", tuple(dimensions))

    @classmethod
    def from_params(cls, params: Mapping[str, Any], label: str = "module.params") -> "ParamSpace":
        warnings.warn("ParamSpace.from_params() is deprecated; use ParamSpace(config=...)", DeprecationWarning, stacklevel=2)
        return cls(config=params, label=label)

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
    if set(raw) not in ({"values"}, {"values", "start"}, {"range"}, {"range", "start"}):
        raise ValueError(f"{label} variable wrapper must contain exactly values or range, with optional start")
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
    start = _json_copy(raw["start"] if "start" in raw else values[0], f"{label}.start")
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
    report: str | None = None


# Compatibility name; canonical documentation and custom SampleTrials need not use it.
SampleTrialResult = SampleResult


class PlaybookOptimizer:
    """History-driven, score-maximizing Playbook parameter optimizer."""

    def __init__(self, config: Mapping[str, Any], param_space: ParamSpace) -> None:
        config = dict(config)
        unknown = set(config) - _OPTIMIZER_KEYS
        if unknown:
            raise ValueError(f"optimizer contains unsupported structural keys: {', '.join(sorted(unknown))}")
        strategy = config.get("strategy", "grid")
        max_samples = config.get("max_trials", config.get("max_samples"))
        seed = config.get("seed")
        blocks = config.get("blocks")
        if strategy not in _STRATEGIES: raise ValueError(f"unsupported optimizer strategy: {strategy}")
        self.param_space = param_space; self.space = param_space
        self.config = copy.deepcopy(config); self.strategy = strategy; self.max_samples = max_samples
        self.seed = seed
        if max_samples is not None and (not isinstance(max_samples, int) or isinstance(max_samples, bool) or max_samples <= 0):
            raise ValueError("optimizer.max_samples must be a positive integer")
        if strategy != "grid" and max_samples is None:
            raise ValueError(f"max_samples is required for {strategy}")
        if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
            raise ValueError("optimizer.seed must be an integer")
        self.blocks = _coordinate_blocks(param_space, strategy, blocks, "optimizer.blocks")
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

    def next_param_sample(self, history: Sequence[SampleTrialResult]) -> ParamSample | None:
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
            return item.score
        valid = [item for item in history if item.error is None and item.status == "succeeded"
                 and not isinstance(score(item), bool) and isinstance(score(item), (int, float))
                 and math.isfinite(score(item))]
        if not valid:
            return None
        return max(valid, key=score)

    def best_param_sample(self, history: Sequence[SampleTrialResult]) -> ParamSample | None:
        best = self.best_result(history)
        return best.sample if best is not None else None

    # Compatibility aliases for the 0.3 public surface.
    def next_sample(self, history: Sequence[SampleTrialResult]) -> ParamSample | None:
        return self.next_param_sample(history)

    def best_sample(self, history: Sequence[SampleTrialResult]) -> ParamSample | None:
        return self.best_param_sample(history)

    def propose(self, history: Sequence[SampleTrialResult]) -> ParamSample | None:
        return self.next_param_sample(history)

    def observe(self, history: Sequence[SampleTrialResult], result: SampleTrialResult) -> None:
        if result.sample.key() in {item.sample.key() for item in history[:-1]}:
            raise ValueError("optimizer proposed a duplicate ParamSample")

    @staticmethod
    def _short_sample(sample: ParamSample, limit: int = 72) -> str:
        parts, used = [], 0
        for key, value in sample.values.items():
            part = f"{key}={json.dumps(value, ensure_ascii=False)}"
            extra = len(part) + (3 if parts else 0)
            if parts and used + extra > limit:
                return " / ".join(parts) + " / …"
            parts.append(part); used += extra
        return " / ".join(parts)

    def render_report(self, *, history: Sequence[SampleTrialResult], best_param_sample: ParamSample | None,
                      dataset_report: Any = None) -> str:
        metrics = [name for name in ("precision", "recall", "f1") if any(name in item.metrics for item in history)]
        headers = ["#", "Param Sample", "Score", *[name.title() for name in metrics], "Report"]
        lines = ["# Optimization Progress Report", ""]
        if dataset_report is not None:
            if not dataset_report.path:
                raise ValueError("dataset report must have a saved path")
            lines.extend((f"[Trial Dataset Report]({dataset_report.path})", ""))
        lines.extend(("| " + " | ".join(headers) + " |", "|" + "|".join("---:" if i not in (1, len(headers)-1) else "---" for i in range(len(headers))) + "|"))
        for ordinal, item in enumerate(history, 1):
            best = best_param_sample is not None and item.sample.key() == best_param_sample.key()
            label = "Best" if best else "Details"
            report_link = Path(item.report).name if item.report else ""
            values = [str(ordinal), self._short_sample(item.sample), str(item.score), *[f"{item.metrics.get(name, 0):.3f}" for name in metrics], f"[{label}]({report_link})"]
            if best: values = [f"**{value}**" for value in values]
            lines.append("| " + " | ".join(values) + " |")
        return "\n".join(lines) + "\n"

    def render_optimization_progress(self, *, history: Sequence[SampleTrialResult],
                                     best_param_sample: ParamSample | None) -> str:
        """Explain optimizer state without duplicating the samples table."""
        if not history:
            return "No optimization progress details available."
        selected = next((index for index, result in enumerate(history, 1)
                         if best_param_sample is not None and result.sample.key() == best_param_sample.key()), None)
        message = f"{len(history)} sample(s) evaluated using {self.strategy}."
        if selected is not None:
            message += f" Sample {selected} has the best available score."
        return message


ModuleOptimizer = PlaybookOptimizer


class ParamSampler(PlaybookOptimizer):
    """Deprecated 0.3 constructor retained as a migration aid."""

    def __init__(self, space: ParamSpace, strategy: str = "grid", *, max_samples: int | None = None,
                 seed: int | None = None, blocks: Sequence[Sequence[str]] | None = None, **legacy: Any) -> None:
        warnings.warn("ParamSampler is deprecated; use PlaybookOptimizer", DeprecationWarning, stacklevel=2)
        super().__init__({"strategy": strategy, "max_samples": max_samples, "seed": seed, "blocks": blocks}, space)
        self._legacy_metric = legacy.get("objective_metric", "score")
        self._legacy_direction = legacy.get("direction", "maximize")


@dataclass
class PlaybookTrialResult:
    history: list[SampleTrialResult] = field(default_factory=list)

    def best(self, metric: str, direction: str) -> SampleTrialResult | None:
        valid = [item for item in self.history if item.error is None and metric in item.metrics
                 and not isinstance(item.metrics[metric], bool)
                 and isinstance(item.metrics[metric], (int, float)) and math.isfinite(item.metrics[metric])]
        if not valid: return None
        return (max if direction == "maximize" else min)(valid, key=lambda item: item.metrics[metric])


def run_playbook_trial(*, optimizer: PlaybookOptimizer | None = None, sampler: PlaybookOptimizer | None = None,
                       dataset: Iterable[Any], run: Callable[[ParamSample, Any], Any],
                       evaluator: Callable[[ParamSample, Sequence[Any]], Any], on_sample=None,
                       metric: str | None = None, direction: str | None = None) -> PlaybookTrialResult:
    """Execute the required propose -> runs -> evaluate -> observe outer loop."""
    optimizer = optimizer or sampler
    if optimizer is None:
        raise ValueError("optimizer is required")
    result = PlaybookTrialResult()
    items = list(dataset)
    while (sample := optimizer.next_param_sample(result.history)) is not None:
        if sample.key() in {trial.sample.key() for trial in result.history}:
            raise ValueError("optimizer proposed a duplicate ParamSample")
        started = datetime.now(timezone.utc).isoformat()
        runs = []
        for item in items:
            try:
                runs.append(run(sample, item))
            except Exception as failure:
                runs.append({"item": item, "prediction": None, "error": str(failure)})
        metrics: dict[str, float] = {}
        feedback = error = score = None
        try:
            evaluated = evaluator(sample, runs)
            if isinstance(evaluated, Mapping) and metric is not None:
                metrics_raw = evaluated
                score = evaluated.get(metric)
                if direction == "minimize" and isinstance(score, (int, float)):
                    score = -score
                feedback = None
            elif not isinstance(evaluated, tuple) or len(evaluated) not in {2, 3}:
                raise ValueError("evaluator must return (metrics, score) or (metrics, score, feedback)")
            elif len(evaluated) == 3:
                metrics_raw, score, feedback = evaluated
            else:
                metrics_raw, score = evaluated; feedback = None
            if not isinstance(metrics_raw, Mapping): raise ValueError("evaluator must return a metric map or (metric map, feedback)")
            for name, value in metrics_raw.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"evaluator metric {name!r} must be a finite number")
                metrics[str(name)] = float(value)
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError("evaluator score must be one finite number")
            feedback = _json_copy(feedback, "evaluator feedback")
        except Exception as failure:
            error = str(failure)
            feedback = None
        score = float(score) if error is None else None
        trial = SampleTrialResult(sample, runs, metrics, feedback, error, started,
                                  datetime.now(timezone.utc).isoformat(), score,
                                  "failed" if error else "succeeded")
        result.history.append(trial); optimizer.observe(result.history, trial)
        if on_sample is not None:
            on_sample(trial, result)
    return result


__all__ = ["ParamDimension", "ParamSample", "ParamSampler", "ParamSpace", "PlaybookOptimizer", "PlaybookTrialResult", "SampleResult", "SampleTrialResult", "run_playbook_trial", "validate_document"]
