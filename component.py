"""Parameterized execution and reporting for ZEMI Component modules."""

from __future__ import annotations

import copy
import html
import inspect
import itertools
import json
import os
import re
import sys
import time
import tomllib
import warnings
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from . import env
from .playbook import PLAYBOOK_OUTPUT_MIME, _output_context, validate_output_params
from .params import ParamSample, ParamSpace, PlaybookOptimizer, validate_document


def _canonical_runtime(document: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a Params 0.6 document and adapt it to the established runner."""
    canonical = validate_document(document)
    reference_document: dict[str, Any] = {
        "system": canonical["system"],
        "component": canonical["component"],
        "arsenals": {item["id"]: item for item in canonical["arsenals"]},
        "modules": {item["id"]: item for item in canonical["modules"]},
    }

    def validate_refs(value: Any, label: str) -> None:
        if isinstance(value, Mapping):
            if "ref" in value:
                path = value.get("ref")
                parts = path.split(".") if isinstance(path, str) else []
                allowed = (
                    parts[:2] in (["system", "params"], ["component", "params"])
                    or len(parts) >= 3 and parts[0] in {"arsenals", "modules"} and parts[2] == "params"
                )
                if not allowed:
                    raise ValueError(f"{label}: refs may target only params sections, got {path!r}")
            for key, item in value.items():
                validate_refs(item, f"{label}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value): validate_refs(item, f"{label}[{index}]")

    def resolved_params(table: Mapping[str, Any], label: str, owner: str) -> dict[str, Any]:
        validate_refs(table, label)
        raw, origins = _ParamReferenceResolver(reference_document).resolve_table(table, label)
        variants = _resolve_playbook_params(raw, label, owner, origins)
        if len(variants) != 1:
            raise ValueError(f"{label} must not use legacy each wrappers in Params 0.6")
        return variants[0][0]

    system_params = resolved_params(canonical["system"]["params"], "system.params", "system")
    reference_document["system"]["params"] = system_params
    component_params = resolved_params(canonical["component"]["params"], "component.params", "component")
    reference_document["component"]["params"] = component_params
    for arsenal in canonical["arsenals"]:
        label = f"arsenals.{arsenal['id']}.params"
        arsenal["params"] = resolved_params(arsenal["params"], label, arsenal["id"])
        reference_document["arsenals"][arsenal["id"]]["params"] = arsenal["params"]

    groups: dict[str | None, dict[str, Any]] = {}
    for arsenal in canonical["arsenals"]:
        groups[arsenal["id"]] = {
            "name": arsenal["id"],
            "arsenal_config_path": arsenal.get("config_path"),
            "arsenal_start_and_stop_at_job_level": arsenal["lifecycle"] == "job",
            "playbooks_params": [],
        }
    for playbook in canonical["modules"]:
        label = f"modules.{playbook['id']}.params"
        playbook_params = resolved_params(playbook["params"], label, playbook["id"])
        reference_document["modules"][playbook["id"]]["params"] = playbook_params
        candidate_space = ParamSpace(config=playbook_params, label=label)
        space = candidate_space if candidate_space.dimensions else None
        optimizer_config = playbook.get("optimizer")
        if optimizer_config is not None and isinstance(optimizer_config.get("mode"), Mapping):
            selected, _ = _resolve_playbook_params(
                {"mode": optimizer_config["mode"]},
                f"modules.{playbook['id']}.optimizer",
                playbook["id"],
            )[0]
            optimizer_config["mode"] = selected["mode"]
        if space is None:
            if optimizer_config is not None:
                raise ValueError(f"modules.{playbook['id']}.optimizer is not allowed because its params are all fixed")
            samples = [copy.deepcopy(playbook_params)]
        else:
            names = ", ".join(dimension.name for dimension in space.dimensions)
            if optimizer_config is None:
                raise ValueError(
                    f"modules.{playbook['id']}.optimizer is required because its params "
                    f"define variable dimensions: {names}"
                )
            samples = [space.start]
            trial_config = optimizer_config["sample_trial"]
            trial_config["params"] = resolved_params(
                trial_config["params"],
                f"modules.{playbook['id']}.optimizer.sample_trial.params",
                playbook["id"],
            )
            PlaybookOptimizer(config=optimizer_config, param_space=space)
        arsenal_id = playbook.get("arsenal")
        if arsenal_id is None:
            groups.setdefault(None, {
                "name": None,
                "arsenal_config_path": None,
                "arsenal_start_and_stop_at_job_level": False,
                "playbooks_params": [],
                "_v05_no_arsenal": True,
            })
        groups[arsenal_id]["playbooks_params"].append({
            "playbook_name": playbook["path"].removeprefix("@comp/"),
            "playbook_id": playbook["id"],
            "module_id": playbook["id"],
            "module_kind": playbook["kind"],
            "enabled": playbook.get("enabled", True),
            "playbook_params": playbook_params,
            "_v05_samples": [
                copy.deepcopy(dict(sample.values) if isinstance(sample, ParamSample) else sample)
                for sample in samples
            ],
            "_v05_optimizer": copy.deepcopy(optimizer_config),
            "_v05_space": copy.deepcopy(playbook_params) if space is not None else None,
            "_v05_arsenal": arsenal_id,
            "_v05_lifecycle": (
                reference_document["arsenals"][arsenal_id]["lifecycle"]
                if arsenal_id is not None else None
            ),
        })
    component = canonical["component"]
    return {
        "pipeline_params": system_params,
        "component_params": {
            "component_name": component.get("name"),
            "stop_on_error": component.get("stop_on_error", True),
            **component_params,
        },
        "arsenals": list(groups.values()),
        "_params_06": canonical,
    }


def _playbook_name(item: object, index: int) -> str:
    if not isinstance(item, Mapping):
        raise ValueError(f"playbooks_params[{index}] must be a table")
    name = item.get("playbook_name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"playbooks_params[{index}].playbook_name must be a non-empty string")
    return name


def _resolve_params_path(value: str | Path, root: Path) -> Path:
    params = root / "params"
    label = str(value).replace("\\", "/")
    path = root / label.removeprefix("@comp/") if label.startswith("@comp/") else Path(value)
    if not path.is_absolute():
        path = params / path
    resolved = path.resolve()
    try:
        resolved.relative_to(params.resolve())
    except ValueError:
        raise ValueError("Parameter file must be inside @comp/params") from None
    if resolved.suffix.lower() != ".toml":
        raise ValueError("Parameter file must have the .toml extension")
    if not resolved.is_file():
        raise FileNotFoundError(f"Parameter file was not found: {resolved}")
    return resolved


def _resolve_params_glob(value: str | Path, root: Path) -> list[Path]:
    params = (root / "params").resolve()
    label = str(value).replace("\\", "/")
    if label.startswith("@comp/"):
        label = label.removeprefix("@comp/")
        if not label.startswith("params/"):
            raise ValueError("Parameter glob must be inside @comp/params")
        label = label.removeprefix("params/")
    pattern = Path(label)
    if pattern.is_absolute() or ".." in pattern.parts:
        raise ValueError("Parameter glob must be relative to @comp/params")
    if pattern.suffix.lower() != ".toml":
        raise ValueError("Parameter glob must select TOML files")
    matches = [p.resolve() for p in sorted(params.glob(label), key=lambda p: p.name.casefold()) if p.is_file()]
    if not matches:
        raise FileNotFoundError(f"Parameter glob matched no files: {value}")
    return matches


def _params_candidates(root: Path, value: str | Path | Sequence[str | Path] | None) -> list[Path]:
    if value is None:
        return sorted((p.resolve() for p in (root / "params").glob("*.toml") if p.is_file()), key=lambda p: (p.name != "default_params.toml", p.name.casefold()))
    if isinstance(value, (str, Path)):
        values = [value]
    elif isinstance(value, Sequence):
        values = list(value)
        if not values:
            raise ValueError("params_file sequence must not be empty")
    else:
        raise TypeError("params_file must be a path, glob, sequence of paths/globs, or None")
    result: list[Path] = []
    for item in values:
        if not isinstance(item, (str, Path)):
            raise TypeError("Every params_file item must be a string or Path")
        result.extend(_resolve_params_glob(item, root) if any(c in str(item) for c in "*?[") else [_resolve_params_path(item, root)])
    return list(dict.fromkeys(result))


def _select_params_path(root: Path, value: str | Path | Sequence[str | Path] | None) -> Path:
    candidates = _params_candidates(root, value)
    if not candidates:
        raise FileNotFoundError("No TOML parameter files were found in @comp/params")
    if len(candidates) == 1:
        return candidates[0]
    print("Available component parameter files:")
    for index, path in enumerate(candidates, 1):
        print(f"  {index}. {path.name}")
    try:
        choice = input("Select a parameter file by number or name: ").strip()
    except EOFError:
        raise RuntimeError("Multiple parameter files were found. Set params_file explicitly for non-interactive execution.") from None
    if choice.isdecimal() and 0 <= int(choice) - 1 < len(candidates):
        return candidates[int(choice) - 1]
    for path in candidates:
        if path.name == choice:
            return path
    raise ValueError(f"Invalid parameter file selection: {choice!r}")


def _json_value(value: Any, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain only finite JSON-serializable values: {error}") from error


class _ParamReferenceResolver:
    """Resolve ref wrappers and table includes against one loaded TOML document."""

    _WRAPPER_KEYS = {"ref", "each", "select", "input"}

    def __init__(self, document: Mapping[str, Any]) -> None:
        self.document = document

    def resolve_table(self, table: Mapping[str, Any], label: str, stack: tuple[str, ...] = ()) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        if not isinstance(table, Mapping):
            raise ValueError(f"{label} must be a table")
        result: dict[str, Any] = {}
        origins: dict[str, dict[str, Any]] = {}
        if "__include__" in table:
            wrappers = table["__include__"]
            if isinstance(wrappers, Mapping):
                wrappers = [wrappers]
            elif not isinstance(wrappers, list):
                raise ValueError(f"{label}.__include__ must be a ref wrapper or an array of ref wrappers")
            if not wrappers:
                raise ValueError(f"{label}.__include__ must not be an empty array")
            for index, wrapper in enumerate(wrappers):
                include_label = f"{label}.__include__[{index}]"
                path = self._ref_path(wrapper, include_label)
                included, nested_origins = self._resolve_reference(path, include_label, stack)
                if not isinstance(included, Mapping):
                    raise ValueError(f"{include_label}: ref {path!r} cannot be included because it does not resolve to a table")
                for key, value in included.items():
                    result[key] = copy.deepcopy(value)
                    refs = [path]
                    nested = nested_origins.get(key)
                    if nested:
                        refs.extend(nested.get("refs", []))
                    origins[key] = {"source": "include", "refs": list(dict.fromkeys(refs)), "value": copy.deepcopy(value)}
        for key, raw in table.items():
            if key == "__include__":
                continue
            value, refs, _direct = self.resolve_value(raw, f"{label}.{key}", stack)
            result[key] = value
            if refs:
                origins[key] = {"source": "ref", "refs": refs, "value": copy.deepcopy(value)}
            else:
                origins.pop(key, None)
        return result, origins

    def resolve_value(self, value: Any, label: str, stack: tuple[str, ...] = ()) -> tuple[Any, list[str], bool]:
        if isinstance(value, Mapping):
            wrapper_keys = set(value) & self._WRAPPER_KEYS
            if "ref" in wrapper_keys:
                path = self._ref_path(value, label)
                resolved, _origins = self._resolve_reference(path, label, stack)
                return copy.deepcopy(resolved), [path], True
            if wrapper_keys:
                if len(wrapper_keys) != 1 or set(value) != wrapper_keys:
                    mode = "/".join(sorted(wrapper_keys))
                    raise ValueError(f"{label}: {mode} wrapper must contain exactly one mode key")
                mode = next(iter(wrapper_keys))
                choices = value[mode]
                if not isinstance(choices, list):
                    return copy.deepcopy(dict(value)), [], False
                resolved_choices = []
                refs: list[str] = []
                for index, choice in enumerate(choices):
                    resolved, item_refs, _direct = self.resolve_value(choice, f"{label}.{mode}[{index}]", stack)
                    resolved_choices.append(resolved)
                    refs.extend(item_refs)
                return {mode: resolved_choices}, list(dict.fromkeys(refs)), False
            resolved, origins = self.resolve_table(value, label, stack)
            refs = [ref for metadata in origins.values() for ref in metadata.get("refs", [])]
            return resolved, list(dict.fromkeys(refs)), False
        if isinstance(value, list):
            resolved_items = []
            refs: list[str] = []
            for index, item in enumerate(value):
                resolved, item_refs, _direct = self.resolve_value(item, f"{label}[{index}]", stack)
                resolved_items.append(resolved)
                refs.extend(item_refs)
            return resolved_items, list(dict.fromkeys(refs)), False
        return copy.deepcopy(value), [], False

    def _ref_path(self, wrapper: Any, label: str) -> str:
        if not isinstance(wrapper, Mapping) or set(wrapper) != {"ref"}:
            raise ValueError(f"{label}: ref wrapper must contain exactly one key 'ref'")
        path = wrapper["ref"]
        if not isinstance(path, str) or not path or any(not part for part in path.split(".")):
            raise ValueError(f"{label}.ref must be a non-empty dotted path")
        return path

    def _lookup(self, path: str, label: str) -> Any:
        current: Any = self.document
        for part in path.split("."):
            if not isinstance(current, Mapping):
                raise ValueError(f"{label}: ref {path!r} cannot traverse {part!r} through a non-table value")
            if part not in current:
                raise ValueError(f"{label}: ref path {path!r} was not found")
            current = current[part]
        return current

    def _resolve_reference(self, path: str, label: str, stack: tuple[str, ...]) -> tuple[Any, dict[str, dict[str, Any]]]:
        if path in stack:
            cycle = " -> ".join((*stack, path))
            raise ValueError(f"{label}: cyclic ref detected: {cycle}")
        raw = self._lookup(path, label)
        next_stack = (*stack, path)
        if isinstance(raw, Mapping):
            if "ref" in raw:
                nested = self._ref_path(raw, f"ref {path!r}")
                return self._resolve_reference(nested, f"ref {path!r}", next_stack)
            return self.resolve_table(raw, f"ref {path!r}", next_stack)
        resolved, _refs, _direct = self.resolve_value(raw, f"ref {path!r}", next_stack)
        return resolved, {}


def _resolve_playbook_params(
    params: Mapping[str, Any], label: str, playbook_name: str,
    reference_origins: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[tuple[dict[str, Any], dict[str, dict[str, Any]]]]:
    """Resolve select wrappers, then expand exact each wrappers."""
    literal: dict[str, Any] = {}
    axes: list[tuple[str, list[Any]]] = []
    resolved: dict[str, dict[str, Any]] = copy.deepcopy(dict(reference_origins or {}))
    for name, value in params.items():
        wrapper_keys = set(value) & {"each", "select", "input"} if isinstance(value, Mapping) else set()
        if wrapper_keys:
            if len(wrapper_keys) != 1 or set(value) != wrapper_keys:
                mode = "/".join(sorted(wrapper_keys)) if len(wrapper_keys) > 1 else next(iter(wrapper_keys))
                raise ValueError(f"{label}.{name}: {mode} wrapper must contain exactly one mode key")
            mode = next(iter(wrapper_keys))
            if mode == "input":
                selected = _resolve_input(value, f"{label}.{name}", playbook_name)
                literal[name] = selected
                secret = isinstance(value.get("input"), Mapping) and value["input"].get("secret") is True
                resolved[name] = {"source": "input", "value": "***" if secret else copy.deepcopy(selected),
                                  "secret": secret}
                continue
            choices = value[mode]
            if not isinstance(choices, list):
                raise ValueError(f"{label}.{name}.{mode} must be an array")
            if not choices:
                raise ValueError(f"{label}.{name}.{mode} must not be empty")
            normalized = [_json_value(v, f"{label}.{name}.{mode}") for v in choices]
            if mode == "each":
                axes.append((name, normalized))
                continue
            print(f"Select one value for playbook {playbook_name!r}, parameter {name!r}:")
            for index, choice in enumerate(normalized, 1):
                print(f"  {index}. {json.dumps(choice, ensure_ascii=False)}")
            try:
                answer = input(
                    f"Playbook {playbook_name!r}, parameter {name!r} (1-{len(normalized)}): "
                ).strip()
            except EOFError:
                raise RuntimeError(
                    f"Cannot select a value for playbook {playbook_name!r}, parameter {name!r}: interactive input is unavailable"
                ) from None
            if not answer.isdecimal() or not 1 <= int(answer) <= len(normalized):
                raise ValueError(
                    f"Invalid selection {answer!r} for playbook {playbook_name!r}, parameter {name!r}; expected a number from 1 to {len(normalized)}"
                )
            selected = _resolve_nested_inputs(
                copy.deepcopy(normalized[int(answer) - 1]),
                f"{label}.{name}.select[{int(answer) - 1}]",
                playbook_name,
            )
            literal[name] = selected
            metadata = {"source": "select", "value": copy.deepcopy(selected)}
            if name in resolved and resolved[name].get("refs"):
                metadata["refs"] = copy.deepcopy(resolved[name]["refs"])
            resolved[name] = metadata
        else:
            literal[name] = _json_value(value, f"{label}.{name}")
    if not axes:
        return [(literal, resolved)]
    expanded = []
    for values in itertools.product(*(axis[1] for axis in axes)):
        trial = copy.deepcopy(literal)
        trial.update((axes[i][0], copy.deepcopy(value)) for i, value in enumerate(values))
        origins = copy.deepcopy(resolved)
        for i, value in enumerate(values):
            name = axes[i][0]
            metadata = {"source": "each", "value": copy.deepcopy(value)}
            if name in origins and origins[name].get("refs"):
                metadata["refs"] = copy.deepcopy(origins[name]["refs"])
            origins[name] = metadata
        expanded.append((trial, origins))
    return expanded


def _resolve_nested_inputs(value: Any, label: str, playbook_name: str) -> Any:
    if isinstance(value, Mapping):
        if "input" in value:
            if set(value) != {"input"}:
                raise ValueError(f"{label}: input wrapper must contain exactly one key 'input'")
            return _resolve_input(value, label, playbook_name)
        return {
            key: _resolve_nested_inputs(item, f"{label}.{key}", playbook_name)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_nested_inputs(item, f"{label}[{index}]", playbook_name)
            for index, item in enumerate(value)
        ]
    return value


def _resolve_input(wrapper: Mapping[str, Any], label: str, playbook_name: str) -> Any:
    from .inputs import InputError, InputStore
    specification = wrapper["input"]
    if isinstance(specification, str):
        specification = {"prompt": specification}
    if not isinstance(specification, Mapping):
        raise ValueError(f"{label}.input must be a prompt string or a table")
    allowed = {"prompt", "type", "default", "suggested", "env", "validate", "secret"}
    unexpected = set(specification) - allowed
    if unexpected:
        raise ValueError(f"{label}.input contains unsupported keys: {', '.join(sorted(unexpected))}")
    prompt = specification.get("prompt", "Enter a value")
    value_type = specification.get("type", "string")
    if not isinstance(prompt, str) or not prompt:
        raise ValueError(f"{label}.input.prompt must be a non-empty string")
    if value_type not in {"string", "path", "integer", "float", "boolean", "json"}:
        raise ValueError(f"{label}.input.type must be string, path, integer, float, boolean, or json")
    if "env" in specification and (not isinstance(specification["env"], str) or not specification["env"]):
        raise ValueError(f"{label}.input.env must be a non-empty persistent input key")
    if "secret" in specification and not isinstance(specification["secret"], bool):
        raise ValueError(f"{label}.input.secret must be boolean")
    store_spec = dict(specification)
    if "default" in store_spec and "suggested" not in store_spec:
        default = store_spec.pop("default")
        store_spec["suggested"] = json.dumps(default, ensure_ascii=False) if value_type == "json" else str(default)
    try:
        answer = InputStore().resolve(store_spec)
    except InputError:
        raise RuntimeError(
            f"Cannot input a value for playbook {playbook_name!r} at {label}: interactive input is unavailable"
        ) from None
    try:
        if value_type in {"string", "path"}:
            return answer
        if value_type == "integer":
            return int(answer)
        if value_type == "float":
            return _json_value(float(answer), label)
        if value_type == "boolean":
            normalized = answer.strip().casefold()
            if normalized not in {"true", "false"}:
                raise ValueError("expected true or false")
            return normalized == "true"
        return _json_value(json.loads(answer), label)
    except ValueError as error:
        raise ValueError(f"Invalid {value_type} input for {label}: {answer!r} ({error})") from error


def _trial_id(name: str, config_index: int, trial_index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.casefold()).strip("-") or "playbook"
    return f"p{config_index + 1:03d}-t{trial_index + 1:04d}-{slug[:48]}"


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _summarize_trials(trials: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = ("succeeded", "failed", "running")
    ids = [trial.get("trial_id") for trial in trials if trial.get("trial_id") is not None]
    trial_ids = {"total": ids}
    trial_ids.update(
        (status, [trial.get("trial_id") for trial in trials if trial.get("status") == status])
        for status in statuses
    )
    return {
        "counts": {name: len(values) for name, values in trial_ids.items()},
        "trial_ids": trial_ids,
    }


class ComponentReport:
    """Canonical JSON report with HTML and Markdown renderings."""

    def __init__(self, component_name: str, component_root: Path, run_directory: Path, params_file: str, pipeline_params: Mapping[str, Any]) -> None:
        self.path = run_directory / "report.json"
        self.main_path = run_directory / "main.md"
        self.markdown_path = run_directory / "report.md"
        self.run_directory = run_directory
        self._sample_trial_markdown: dict[str, str] = {}
        self._secret_values: set[str] = set()
        trials: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {"schema_version": 1, "component_name": component_name, "component_root": str(component_root), "params_file": params_file, "pipeline_params": copy.deepcopy(dict(pipeline_params)), "started_at": _timestamp(), "finished_at": None, "status": "running", "trials": trials, "playbooks": trials, "summary": {}}

    def start_trial(self, playbook: "Playbook") -> dict[str, Any]:
        input_params = copy.deepcopy(playbook.params)
        for name in playbook.secret_param_names:
            if name in input_params:
                if str(input_params[name]):
                    self._secret_values.add(str(input_params[name]))
                input_params[name] = "***"
        entry = {"trial_id": playbook.trial_id, "playbook_name": playbook.playbook_name, "arsenal": playbook.arsenal_id, "input_params": input_params, "resolved_params": copy.deepcopy(playbook.resolved_params), "output_params": {}, "output_notebook": playbook.output_relative.as_posix(), "output_html": playbook.output_html_relative.as_posix(), "output_markdown": playbook.output_markdown_relative.as_posix(), "output_path": playbook.output_relative.as_posix(), "started_at": _timestamp(), "finished_at": None, "duration_seconds": None, "status": "running", "error": None}
        self.data["trials"].append(entry)
        self.save()
        return entry

    def start_playbook(self, name: str, output_path: Path) -> dict[str, Any]:
        entry = {"playbook_name": name, "output_path": str(output_path), "started_at": _timestamp(), "status": "running"}
        self.data["trials"].append(entry); self.save(); return entry

    @staticmethod
    def finish_playbook(entry: dict[str, Any]) -> None:
        entry.update(status="succeeded", finished_at=_timestamp())

    @staticmethod
    def fail_playbook(entry: dict[str, Any], error: BaseException) -> None:
        entry.update(status="failed", finished_at=_timestamp(), error=_error_data(error))

    def record_failure(self, error: BaseException) -> None:
        self.data["status"] = "failed"; self.data["error"] = _error_data(error); self.save()

    def set_sample_trial_markdown(self, playbook_id: str, markdown: str) -> None:
        for secret in self._secret_values:
            markdown = markdown.replace(secret, "***")
        self._sample_trial_markdown[playbook_id] = markdown

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if "job_trial" in self.data:
            self.data["job_trial"].update({key: self.data[key] for key in ("status", "started_at", "finished_at")})
        self.data["summary"] = _summarize_trials(self.data["trials"])
        rendered_data = _redact_secrets(self.data, self._secret_values)
        json_tmp = self.path.with_name(".report.json.tmp")
        json_tmp.write_text(json.dumps(rendered_data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(json_tmp, self.path)
        main_tmp = self.main_path.with_name(".main.md.tmp")
        main_tmp.write_text(_main_markdown(rendered_data, self._sample_trial_markdown), encoding="utf-8")
        os.replace(main_tmp, self.main_path)
        markdown_tmp = self.markdown_path.with_name(".report.md.tmp")
        markdown_tmp.write_text(_report_markdown(rendered_data, self._sample_trial_markdown), encoding="utf-8")
        os.replace(markdown_tmp, self.markdown_path)
        for trial in rendered_data["trials"]:
            relative = trial.get("output_markdown")
            if not relative:
                continue
            output_path = self.run_directory / relative
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_tmp = output_path.with_name(f".{output_path.name}.tmp")
            output_tmp.write_text(_trial_markdown(trial), encoding="utf-8")
            os.replace(output_tmp, output_path)
        for trial in rendered_data.get("job_trial", {}).get("playbook_trials", []):
            relative = trial.get("report_markdown")
            if not relative:
                continue
            output_path = self.run_directory / relative
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_tmp = output_path.with_name(f".{output_path.name}.tmp")
            section = self._sample_trial_markdown.get(trial["playbook_id"], "")
            document = {"job_trial": {"playbook_trials": [trial]}}
            output_tmp.write_text(f"# {trial['playbook_id']} SampleTrial report\n" +
                                  _sampling_markdown(document, {trial["playbook_id"]: section}),
                                  encoding="utf-8")
            os.replace(output_tmp, output_path)


class Module:
    """Base contract for one executable unit owned by a Component."""

    kind: str

    def run(self) -> None:
        raise NotImplementedError


class Playbook(Module):
    """A ``kind = 'playbook'`` Module executed through Papermill."""

    kind = "playbook"

    def __init__(self, component: "ZemiComponent", config: Mapping[str, Any], *, config_index: int = 0, trial_index: int = 0, params: Mapping[str, Any] | None = None, resolved_params: Mapping[str, Any] | None = None) -> None:
        self.component = component; self.config = copy.deepcopy(dict(config))
        self.playbook_name = _playbook_name(config, config_index)
        self.module_id = config.get("module_id", config.get("playbook_id"))
        self.enabled = config.get("enabled", True)
        if not isinstance(self.enabled, bool):
            raise ValueError(f"enabled must be boolean for {self.playbook_name!r}")
        self.arsenal_id = config.get("_v05_arsenal")
        configured = config.get("playbook_params", {}) if params is None else params
        if not isinstance(configured, Mapping):
            raise ValueError(f"playbook_params must be a table for {self.playbook_name!r}")
        self.params = copy.deepcopy(dict(configured))
        self.resolved_params = copy.deepcopy(dict(resolved_params or {}))
        self.secret_param_names = {name for name, metadata in self.resolved_params.items()
                                   if isinstance(metadata, Mapping) and metadata.get("secret") is True}
        relative = Path(self.playbook_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid playbook_name: {self.playbook_name!r}")
        self.source_path = component.root / relative
        if not self.source_path.is_file():
            raise FileNotFoundError(f"Playbook notebook was not found: {self.source_path}")
        self.trial_id = _trial_id(self.playbook_name, config_index, trial_index)
        self.output_relative = Path("notebooks") / f"{self.trial_id}.ipynb"
        self.output_path = component.run_directory / self.output_relative
        self.output_html_relative = self.output_relative.with_suffix(".html")
        self.output_html_path = component.run_directory / self.output_html_relative
        self.output_markdown_relative = self.output_relative.with_suffix(".report.md")
        self.output_markdown_path = component.run_directory / self.output_markdown_relative

    def run(self) -> None:
        import papermill
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic(); self._print_start(); entry = self.component.report.start_trial(self)
        try:
            with _output_context(self.component.run_directory):
                papermill.execute_notebook(str(self.source_path), str(self.output_path), parameters=copy.deepcopy(self.params), cwd=str(self.component.root), progress_bar=True, log_output=False, stdout_file=sys.stdout, stderr_file=sys.stderr)
            entry["output_params"] = self._extract_output_params()
            entry["timed_cells"] = self._add_cell_timings()
            self._write_html()
        except Exception as error:
            duration = time.monotonic() - started
            if self.output_path.is_file():
                try:
                    if not entry.get("output_params"):
                        entry["output_params"] = self._extract_output_params()
                except Exception as output_error:
                    entry["output_error"] = _error_data(output_error)
            try:
                entry["timed_cells"] = self._add_cell_timings()
            except Exception as timing_error:
                entry["timing_error"] = _error_data(timing_error)
            try:
                self._write_html()
            except Exception as html_error:
                entry["html_error"] = _error_data(html_error)
            entry["duration_seconds"] = duration
            self.component.report.fail_playbook(entry, error); self.component.report.save(); self._print_failure(error, duration)
            raise
        duration = time.monotonic() - started
        entry["duration_seconds"] = duration; self.component.report.finish_playbook(entry); self.component.report.save(); self._print_success(duration)

    def _extract_output_params(self) -> dict[str, Any]:
        if not self.output_path.is_file():
            return {}
        import nbformat
        notebook = nbformat.read(self.output_path, as_version=4); found = []
        for cell in notebook.cells:
            for output in cell.get("outputs", []):
                data = output.get("data", {})
                if PLAYBOOK_OUTPUT_MIME in data:
                    found.append(data[PLAYBOOK_OUTPUT_MIME])
        if len(found) > 1:
            raise ValueError("Notebook published output_params() more than once")
        return {} if not found else validate_output_params(found[0])

    def _print_start(self) -> None:
        line = "═" * 78
        print(f"\n{line}\nZEMI COMPONENT · PLAYBOOK START\nComponent : {self.component.name}\nPlaybook  : {self.playbook_name}\nTrial     : {self.trial_id}")
        if self.resolved_params:
            print("Resolved parameters:")
            for name, metadata in self.resolved_params.items():
                value = json.dumps(metadata["value"], ensure_ascii=False)
                print(f"  {name} [{metadata['source']}] = {value}")
        print(f"Parameters: {self.component.params_path.relative_to(self.component.root).as_posix()}\nOutput    : {self.output_path.relative_to(self.component.root).as_posix()}\n{line}")

    def _print_success(self, duration: float) -> None:
        line = "═" * 78
        print(f"{line}\n✓ PLAYBOOK COMPLETED · {self.playbook_name} · {self.trial_id}\n  Duration: {_format_duration(duration)}\n  Output  : {self.output_path.relative_to(self.component.root).as_posix()}\n{line}")

    def _print_failure(self, error: BaseException, duration: float) -> None:
        line = "!" * 78
        print(f"{line}\n✗ PLAYBOOK FAILED · {self.playbook_name} · {self.trial_id}\n  Duration: {_format_duration(duration)}\n  Error   : {type(error).__name__}: {error}\n  Output  : {self.output_path.relative_to(self.component.root).as_posix()}\n{line}")

    def _add_cell_timings(self) -> int:
        if not self.output_path.is_file():
            return 0
        import nbformat
        notebook = nbformat.read(self.output_path, as_version=4); cells = []; count = 0
        for cell in notebook.cells:
            if "zemi-cell-timing" in cell.metadata.get("tags", []):
                continue
            cells.append(cell)
            if cell.cell_type != "code":
                continue
            try:
                duration = float(cell.metadata.get("papermill", {}).get("duration"))
            except (TypeError, ValueError):
                continue
            note = nbformat.v4.new_markdown_cell(f"> ⏱ Время выполнения ячейки: **{duration:.3f} с**")
            note.metadata["tags"] = ["zemi-cell-timing"]
            note.metadata["zemi"] = {"source_cell_id": cell.get("id"), "duration_seconds": duration}
            cells.append(note); count += 1
        notebook.cells = cells; nbformat.write(notebook, self.output_path); return count

    def _write_html(self) -> None:
        if not self.output_path.is_file():
            return
        import nbconvert
        from nbconvert import HTMLExporter
        package_path = Path(nbconvert.__file__).resolve()
        template_root = next(
            (
                parent / "share" / "jupyter" / "nbconvert" / "templates"
                for parent in package_path.parents
                if (parent / "share" / "jupyter" / "nbconvert" / "templates" / "lab").is_dir()
            ),
            None,
        )
        options = {} if template_root is None else {"extra_template_basedirs": [str(template_root)], "extra_template_paths": [str(template_root)]}
        body, _resources = HTMLExporter(**options).from_filename(str(self.output_path))
        temporary = self.output_html_path.with_name(f".{self.output_html_path.name}.tmp")
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, self.output_html_path)


class ZemiComponent:
    """Load component parameters and own expanded playbook trial lifecycle."""

    @classmethod
    def from_best_report(
        cls,
        params_file: str | Path | Sequence[str | Path],
        report_file: str | Path,
    ) -> "ZemiComponent":
        """Create a component that replays each reported playbook's best sample."""
        report_path = Path(report_file).expanduser().resolve()
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"best sample report could not be read: {report_path}: {error}") from error
        playbook_trials = report.get("job_trial", {}).get("playbook_trials")
        if not isinstance(playbook_trials, list) or not playbook_trials:
            raise ValueError("best sample report contains no playbook trials")
        overrides: dict[str, Mapping[str, Any]] = {}
        for index, trial in enumerate(playbook_trials):
            if not isinstance(trial, Mapping):
                raise ValueError(f"best sample report playbook_trials[{index}] must be an object")
            playbook_id = trial.get("playbook_id")
            best_id = trial.get("best_sample")
            samples = trial.get("samples")
            if not isinstance(playbook_id, str) or not playbook_id:
                raise ValueError(f"best sample report playbook_trials[{index}].playbook_id is invalid")
            if playbook_id in overrides:
                raise ValueError(f"best sample report contains duplicate playbook id {playbook_id!r}")
            if trial.get("status") != "succeeded" or not isinstance(best_id, str) or not isinstance(samples, list):
                raise ValueError(f"best sample report has no successful best sample for playbook {playbook_id!r}")
            matches = [sample for sample in samples if isinstance(sample, Mapping) and sample.get("sample_trial_id") == best_id]
            if len(matches) != 1 or not isinstance(matches[0].get("params"), Mapping):
                raise ValueError(f"best sample report cannot resolve {best_id!r} for playbook {playbook_id!r}")
            overrides[playbook_id] = copy.deepcopy(dict(matches[0]["params"]))
        return cls(params_file, sample_overrides=overrides)

    def __init__(self, params_file: str | Path | Sequence[str | Path] | None = None, *, sample_overrides: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.root = env.path.comp.root; self.params_path = _select_params_path(self.root, params_file)
        with self.params_path.open("rb") as file:
            self.params = tomllib.load(file)
        self.params_06 = "system" in self.params
        self.params_05 = self.params_06
        if self.params_06:
            self.params = _canonical_runtime(self.params)
            remaining = set(sample_overrides or {})
            for group in self.params["arsenals"]:
                for config in group["playbooks_params"]:
                    identifier = config["playbook_id"]
                    if identifier not in remaining:
                        continue
                    values = validate_output_params(sample_overrides[identifier])
                    if config["_v05_space"] is None:
                        raise ValueError(f"sample_overrides.{identifier}: playbook does not define a ParamSpace")
                    space = ParamSpace(config=config["_v05_space"])
                    if set(values) != set(space.start.values):
                        raise ValueError(f"sample_overrides.{identifier}: expected all configured parameter keys")
                    for key, value in space.fixed.items():
                        if json.dumps(values[key], sort_keys=True) != json.dumps(value, sort_keys=True):
                            raise ValueError(f"sample_overrides.{identifier}.{key}: fixed parameter differs")
                    for dimension in space.dimensions:
                        if json.dumps(values[dimension.name], sort_keys=True) not in [json.dumps(v, sort_keys=True) for v in dimension.values]:
                            raise ValueError(f"sample_overrides.{identifier}.{dimension.name}: value outside domain")
                    config["_v05_space"] = values
                    config["_v05_samples"] = [values]
                    if config["_v05_optimizer"]:
                        config["_v05_optimizer"].update(strategy="grid", max_trials=1)
                        config["_v05_optimizer"].pop("blocks", None)
                    remaining.remove(identifier)
            if remaining:
                raise ValueError(f"sample_overrides: unknown playbook ids {sorted(remaining)}")
        else:
            if sample_overrides:
                raise ValueError("sample_overrides requires Params 0.6")
            warnings.warn(
                "Legacy ZEMI parameter schema is deprecated; migrate to Params 0.6",
                DeprecationWarning,
                stacklevel=2,
            )
        pipeline_resolver = _ParamReferenceResolver(self.params)
        raw_pipeline, pipeline_origins = pipeline_resolver.resolve_table(
            _params_table(self.params, "pipeline_params"),
            "pipeline_params",
        )
        pipeline_variants = _resolve_playbook_params(
            raw_pipeline,
            "pipeline_params",
            "component",
            pipeline_origins,
        )
        if len(pipeline_variants) != 1:
            raise ValueError("pipeline_params must not use each")
        self.pipeline_params = pipeline_variants[0][0]
        self.params["pipeline_params"] = copy.deepcopy(self.pipeline_params)
        reference_resolver = _ParamReferenceResolver(self.params)
        self.component_params = _params_table(self.params, "component_params")
        configured_name = self.component_params.get("component_name")
        if configured_name is None:
            self.name = self.root.name
        elif isinstance(configured_name, str) and configured_name:
            self.name = configured_name
        else:
            raise ValueError("component_params.component_name must be a non-empty string")
        self.stop_on_error = self.component_params.get("stop_on_error", True)
        if not isinstance(self.stop_on_error, bool):
            raise ValueError("component_params.stop_on_error must be boolean")
        self.run_directory = env.path.comp.runid
        self.report = ComponentReport(self.name, self.root, self.run_directory, self.params_path.relative_to(self.root).as_posix(), self.pipeline_params)
        groups = self.params.get("arsenals")
        if groups is not None and "playbooks_params" in self.params:
            raise ValueError("Use either arsenals or the legacy top-level playbooks_params, not both")
        if groups is None:
            configs = self.params.get("playbooks_params", [])
            if not isinstance(configs, list):
                raise ValueError("playbooks_params must be an array of tables")
            legacy_arsenal = self.component_params.get("arsenal", {})
            if not isinstance(legacy_arsenal, Mapping):
                raise ValueError("component_params.arsenal must be a table")
            groups = [{"name": "default", "arsenal_start_and_stop_at_job_level": bool(legacy_arsenal), "arsenal_config_path": legacy_arsenal.get("arsenal_config_path"), "playbooks_params": configs, "_legacy": True}]
            self.arsenal_config_path = legacy_arsenal.get("arsenal_config_path")
        elif not isinstance(groups, list):
            raise ValueError("arsenals must be an array of tables")
        playbooks = []
        self._arsenal_groups: list[tuple[bool, str | None, tuple[Playbook, ...]]] = []
        config_index = 0
        for group_index, group in enumerate(groups):
            if not isinstance(group, Mapping):
                raise ValueError(f"arsenals[{group_index}] must be a table")
            no_arsenal = group.get("_v05_no_arsenal") is True
            name = group.get("name")
            if not no_arsenal and (not isinstance(name, str) or not name):
                raise ValueError(f"arsenals[{group_index}].name must be a non-empty string")
            managed = group.get("arsenal_start_and_stop_at_job_level")
            if not isinstance(managed, bool):
                raise ValueError(f"arsenals[{group_index}].arsenal_start_and_stop_at_job_level must be boolean")
            arsenal_config_path = group.get("arsenal_config_path")
            if managed and (not isinstance(arsenal_config_path, str) or not arsenal_config_path):
                raise ValueError(f"arsenals[{group_index}].arsenal_config_path is required when Arsenal is managed at job level")
            if arsenal_config_path is not None and (not isinstance(arsenal_config_path, str) or not arsenal_config_path):
                raise ValueError(f"arsenals[{group_index}].arsenal_config_path must be a non-empty string")
            configs = group.get("playbooks_params", [])
            if not isinstance(configs, list):
                raise ValueError(f"arsenals[{group_index}].playbooks_params must be an array of tables")
            group_playbooks = []
            for local_index, config in enumerate(configs):
                _playbook_name(config, local_index)
                config = copy.deepcopy(dict(config))
                enabled = config.get("enabled", True)
                if isinstance(enabled, Mapping):
                    if set(enabled) != {"select"}:
                        raise ValueError(
                            f"arsenals[{group_index}].playbooks_params[{local_index}].enabled: "
                            "select wrapper must contain exactly one mode key"
                        )
                    selected, _resolved = _resolve_playbook_params(
                        {"enabled": enabled},
                        f"arsenals[{group_index}].playbooks_params[{local_index}]",
                        config["playbook_name"],
                    )[0]
                    config["enabled"] = selected["enabled"]
                raw = config.get("playbook_params", {})
                if not isinstance(raw, Mapping):
                    raise ValueError(f"arsenals[{group_index}].playbooks_params[{local_index}].playbook_params must be a table")
                label = f"arsenals[{group_index}].playbooks_params[{local_index}].playbook_params"
                raw, reference_origins = reference_resolver.resolve_table(raw, label)
                if no_arsenal:
                    pass
                elif group.get("_legacy"):
                    if arsenal_config_path is not None and raw.get("arsenal_config_path") != arsenal_config_path:
                        raise ValueError(f"{label}.arsenal_config_path must match the shared component_params.arsenal.arsenal_config_path")
                elif managed:
                    for key in ("arsenal_config_path", "arsenal_start_and_stop_at_job_level"):
                        if key in raw:
                            raise ValueError(f"{label}.{key} is controlled by its Arsenal group and cannot be overridden")
                    raw["arsenal_config_path"] = arsenal_config_path
                    raw["arsenal_start_and_stop_at_job_level"] = True
                else:
                    if "arsenal_start_and_stop_at_job_level" in raw:
                        raise ValueError(f"{label}.arsenal_start_and_stop_at_job_level is controlled by its Arsenal group and cannot be overridden")
                    raw["arsenal_start_and_stop_at_job_level"] = False
                    if arsenal_config_path is not None:
                        raw.setdefault("arsenal_config_path", arsenal_config_path)
                if "_v05_samples" in config:
                    variants = [
                        (copy.deepcopy(sample), copy.deepcopy(reference_origins))
                        for sample in config["_v05_samples"]
                    ]
                else:
                    variants = _resolve_playbook_params(raw, label, config["playbook_name"], reference_origins)
                for trial_index, (params, resolved) in enumerate(variants):
                    if "_v05_samples" in config and not no_arsenal:
                        if arsenal_config_path is not None:
                            params["arsenal_config_path"] = arsenal_config_path
                        params["arsenal_start_and_stop_at_job_level"] = config.get("_v05_lifecycle") in {"job", "external"}
                    playbook = Playbook(self, config, config_index=config_index, trial_index=trial_index, params=params, resolved_params=resolved)
                    playbook.playbook_id = config.get("playbook_id")
                    playbook.arsenal_id = config.get("_v05_arsenal")
                    playbook.optimizer_config = copy.deepcopy(config.get("_v05_optimizer"))
                    playbooks.append(playbook); group_playbooks.append(playbook)
                config_index += 1
            self._arsenal_groups.append((managed, arsenal_config_path, tuple(group_playbooks)))
        self.modules = tuple(playbooks)
        self.playbooks = self.modules  # compatibility alias
        self._closed = False; self.report.save()

    def _prepare_sample_trials(self):
        from .sample_trial import resolve_sample_trial
        prepared = {}
        for playbook in self.playbooks:
            if not playbook.enabled or playbook.optimizer_config is None:
                continue
            config = playbook.optimizer_config["sample_trial"]
            try:
                sample_trial = resolve_sample_trial(config, execute=lambda **kwargs: {})
                items = list(sample_trial.load_dataset())
                if not items:
                    raise ValueError("dataset must not be empty")
                ids = set()
                for index, item in enumerate(items):
                    if isinstance(item, dict):
                        item.setdefault("id", index)
                        identity = item["id"]
                    else:
                        identity = index
                    key = json.dumps(identity, sort_keys=True)
                    if key in ids:
                        raise ValueError(f"dataset[{index}].id is duplicated")
                    ids.add(key)
                prepared[playbook.playbook_id] = (items, sample_trial)
            except Exception as error:
                raise ValueError(f"modules.{playbook.playbook_id}.optimizer.sample_trial: {error}") from error
        return prepared

    @staticmethod
    def _activate_managed_model(session, params) -> None:
        model_name = params.get("model_name")
        if model_name is None:
            return
        if not isinstance(model_name, str) or not model_name:
            raise ValueError("model_name must be a non-empty string")
        session.model(model_name)

    def _run_optimization(self, playbook, prepared, session=None):
        items, sample_trial = prepared
        config = playbook.optimizer_config
        optimizer = PlaybookOptimizer(
            config=config,
            param_space=ParamSpace(config=playbook.config["_v05_space"]),
        )
        parent = {"playbook_trial_id": playbook.playbook_id, "playbook_id": playbook.playbook_id,
                  "arsenal": playbook.arsenal_id,
                  "report_markdown": f"sample_trials/{playbook.playbook_id}.report.md",
                  "optimizer": {key: copy.deepcopy(config[key]) for key in ("mode", "strategy", "max_trials", "seed", "blocks") if key in config},
                  "started_at": _timestamp(), "finished_at": None, "status": "running", "samples": [],
                  "ranking": [], "best_sample": None}
        self.report.data.setdefault("job_trial", {"job_trial_id": self.run_directory.name, "playbook_trials": []})["playbook_trials"].append(parent)
        serial = 0

        def execute_item(*, playbook, sample, item, context, runner=None, runner_params=None):
            nonlocal serial
            serial += 1
            if session is not None:
                self._activate_managed_model(session, sample.values)
            started = _timestamp()
            record = {"playbook_run_id": f"{playbook.playbook_id}-run-{serial:06d}", "item": copy.deepcopy(item),
                      "prediction": None, "error": None, "started_at": started, "status": "running"}

            def execute(inputs):
                params = copy.deepcopy(dict(sample.values))
                for key in _SERVICE_INPUT_PARAMS:
                    if key in playbook.params:
                        params[key] = playbook.params[key]
                params.update(copy.deepcopy(inputs))
                child = Playbook(self, playbook.config, config_index=self.playbooks.index(playbook),
                                 trial_index=serial - 1, params=params,
                                 resolved_params=playbook.resolved_params)
                try:
                    child.run()
                finally:
                    entry = next((t for t in reversed(self.report.data["trials"]) if t["trial_id"] == child.trial_id), None)
                    if entry:
                        entry["playbook_run_id"] = record["playbook_run_id"]
                        record["artifacts"] = {key: entry[key] for key in ("output_notebook", "output_html", "output_markdown")}
                return entry["output_params"]

            try:
                item_input = item.get("input") if isinstance(item, Mapping) and "input" in item else item
                if runner is None:
                    prediction = execute({"dataset_input": copy.deepcopy(item_input)})
                else:
                    prediction = runner(sample, copy.deepcopy(item_input), execute=execute,
                                        context=context, params=runner_params or {})
                record["prediction"] = validate_output_params(prediction)
                record["status"] = "succeeded"
            except Exception as error:
                record.update(error=_error_data(error), status="failed")
            record["finished_at"] = _timestamp()
            return record

        sample_trial.execute = execute_item

        history = []

        def save_sample(trial):
            sample_id = f"{playbook.playbook_id}-sample-{len(history):04d}"
            parent["samples"].append({"sample_trial_id": sample_id, "proposal_ordinal": len(history),
                "params": {key: ("***" if key in playbook.secret_param_names else value)
                           for key, value in trial.sample.values.items()},
                "runs": trial.runs, "metrics": trial.metrics, "feedback": trial.feedback,
                "score": trial.score,
                "error": trial.error, "status": trial.status, "artifacts": trial.artifacts,
                "run_errors": sum(r.get("status") == "failed" for r in trial.runs),
                "started_at": trial.started_at, "finished_at": trial.finished_at})
            for record in trial.runs:
                record["sample_trial_id"] = sample_id
            ranked = sorted((s for s in parent["samples"] if s["error"] is None),
                            key=lambda s: s["score"], reverse=True)
            parent["ranking"] = [s["sample_trial_id"] for s in ranked]
            parent["best_sample"] = parent["ranking"][0] if ranked else None
            parent["best_params"] = ranked[0]["params"] if ranked else None
            self.report.save()  # Persist the generic result before domain rendering.
            best = optimizer.best_param_sample(history)
            self.report.set_sample_trial_markdown(
                playbook.playbook_id,
                (sample_trial.render_report(history, best)
                 if len(inspect.signature(sample_trial.render_report).parameters) == 2
                 else sample_trial.render_report(history, parent["optimizer"], best)),
            )
            self.report.save()

        try:
            while (sample := optimizer.next_param_sample(history)) is not None:
                if sample.key() in {item.sample.key() for item in history}:
                    raise ValueError("optimizer proposed a duplicate ParamSample")
                started = _timestamp()
                runs = []
                try:
                    run_parameters = inspect.signature(sample_trial.run).parameters
                    if "module" in run_parameters:
                        runs = sample_trial.run(module=playbook, param_sample=sample, dataset=items)
                    else:
                        warnings.warn(
                            "SampleTrial.run(playbook, sample, dataset) is deprecated; use module and param_sample",
                            DeprecationWarning,
                            stacklevel=2,
                        )
                        runs = sample_trial.run(playbook=playbook, sample=sample, dataset=items)
                    if "dataset" in inspect.signature(sample_trial.evaluate).parameters:
                        evaluated = sample_trial.evaluate(runs=runs, dataset=items)
                    else:
                        warnings.warn(
                            "SampleTrial.evaluate(runs) is deprecated; accept evaluate(runs, dataset)",
                            DeprecationWarning,
                            stacklevel=2,
                        )
                        evaluated = sample_trial.evaluate(runs=runs)
                    if not isinstance(evaluated, tuple) or len(evaluated) != 3:
                        raise ValueError("SampleTrial.evaluate(runs) must return (metrics, score, feedback)")
                    metrics, score, feedback = evaluated
                    trial = sample_trial.result(param_sample=sample, runs=runs, score=score, metrics=metrics,
                                               feedback=feedback, started_at=started, finished_at=_timestamp())
                except Exception as error:
                    trial = sample_trial.result(param_sample=sample, runs=runs, score=None, metrics={},
                                               error=str(error), started_at=started, finished_at=_timestamp())
                history.append(trial)
                save_sample(trial)
                if config.get("mode", "optimize") == "start_only":
                    break
            parent["status"] = "failed" if any(s.error for s in history) else "succeeded"
            if parent["status"] == "failed":
                raise ValueError(f"PlaybookTrial {playbook.playbook_id}: SampleTrial failed; see report")
        except Exception:
            parent["status"] = "failed"
            raise
        finally:
            parent["finished_at"] = _timestamp()
            self.report.save()

    def run(self) -> None:
        first_error: BaseException | None = None
        try:
            prepared = self._prepare_sample_trials()
        except Exception as error:
            self.report.record_failure(error)
            raise
        from . import arsenal
        from .arsenal import ArsenalSession
        for managed, arsenal_config_path, group_playbooks in self._arsenal_groups:
            enabled_playbooks = tuple(playbook for playbook in group_playbooks if playbook.enabled)
            if not enabled_playbooks:
                continue
            session = None
            try:
                if managed:
                    session = ArsenalSession(arsenal_config_path)
                    arsenal.begin(session, stop_before_begin=True)
                for playbook in enabled_playbooks:
                    try:
                        if playbook.optimizer_config is not None:
                            self._run_optimization(playbook, prepared[playbook.playbook_id], session=session)
                        else:
                            if session is not None:
                                self._activate_managed_model(session, playbook.params)
                            playbook.run()
                    except Exception as error:
                        first_error = first_error or error; self.report.record_failure(error)
                        if self.stop_on_error:
                            break
            except Exception as error:
                first_error = first_error or error; self.report.record_failure(error)
            finally:
                if session is not None:
                    try:
                        arsenal.end(session, stop_after_end=True)
                    except Exception as error:
                        first_error = first_error or error; self.report.record_failure(error)
            if first_error is not None and self.stop_on_error:
                raise first_error
        if first_error is not None:
            raise first_error

    def close(self) -> None:
        if self._closed:
            return
        if self.report.data["status"] == "running":
            self.report.data["status"] = (
                "failed"
                if (any(trial.get("status") == "failed" for trial in self.report.data["trials"])
                    or any(run.get("status") == "failed"
                           for trial in self.report.data.get("job_trial", {}).get("playbook_trials", [])
                           for sample in trial["samples"] for run in sample["runs"]))
                else "succeeded"
            )
        self.report.data["finished_at"] = _timestamp()
        self.report.save()
        marker_tmp = self.run_directory / ".complete.tmp"
        marker_tmp.write_bytes(b"")
        os.replace(marker_tmp, self.run_directory / "complete")
        self._closed = True


_SERVICE_INPUT_PARAMS = {"arsenal_config_path", "arsenal_start_and_stop_at_job_level"}


def _human_label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _output_markdown_table(output_params: Mapping[str, Any]) -> str:
    if not output_params:
        return "_No published output._"
    rows = [
        "<table>",
        "<thead><tr><th>Result</th><th>Value</th></tr></thead>",
        "<tbody>",
    ]
    for name, value in output_params.items():
        label = html.escape(_human_label(name))
        displayed = _display_value(value)
        escaped = html.escape(displayed)
        if not isinstance(value, (str, int, float, bool)) or len(displayed) > 100 or "\n" in displayed:
            rendered = (
                f"<details><summary>Show value ({len(displayed)} characters)</summary>"
                f"<pre>{escaped}</pre></details>"
            )
        else:
            rendered = f"<code>{escaped}</code>"
        rows.append(f"<tr><td>{label}</td><td>{rendered}</td></tr>")
    rows.extend(("</tbody>", "</table>"))
    return "\n".join(rows)


def _trial_summary_text(trial: Mapping[str, Any]) -> str:
    parts = [str(trial.get("trial_id") or trial.get("playbook_name") or "Trial")]
    for name, value in trial.get("input_params", {}).items():
        if name in _SERVICE_INPUT_PARAMS or not isinstance(value, (str, int, float, bool)):
            continue
        parts.append(f"{_human_label(name)}: {_display_value(value)}")
    parts.append(f"Status: {trial.get('status', 'unknown')}")
    duration = trial.get("duration_seconds")
    if isinstance(duration, (int, float)):
        parts.append(f"Duration: {_format_duration(duration)}")
    return " · ".join(parts)


def _trial_markdown(trial: Mapping[str, Any]) -> str:
    title = html.escape(str(trial.get("trial_id") or trial.get("playbook_name") or "Trial"))
    summary = html.escape(_trial_summary_text(trial))
    links = []
    for field, label in (("output_html", "Executed notebook HTML"), ("output_notebook", "Executed notebook")):
        target = trial.get(field)
        if target:
            links.append(f"[{label}]({Path(str(target)).name})")
    lines = [f"# {title} output", "", summary, ""]
    if links:
        lines.extend((" · ".join(links), ""))
    lines.extend(("## Published output", "", _output_markdown_table(trial.get("output_params", {})), ""))
    if trial.get("error"):
        lines.extend(("## Error", "", f"```json\n{_display_value(trial['error'])}\n```", ""))
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    displayed = _display_value(value).replace("\r\n", "\n").replace("\r", "\n")
    return displayed.replace("|", "\\|").replace("\n", "<br>")


def _markdown_link(label: str, target: object) -> str:
    return f"[{label}]({str(target).replace(' ', '%20')})"


def _main_markdown(data: Mapping[str, Any], domain_sections: Mapping[str, str] | None = None) -> str:
    trials = data.get("trials") or data.get("playbooks") or []
    lines = [
        "# ZEMI job report",
        "",
        "## Overview",
        "",
        "| Property | Value |",
        "|---|---|",
    ]
    overview = (
        ("Component", data.get("component_name")),
        ("Parameters", data.get("params_file")),
        ("Status", data.get("status")),
        ("Started", data.get("started_at")),
        ("Finished", data.get("finished_at")),
    )
    lines.extend(f"| {name} | {_markdown_cell(value)} |" for name, value in overview)
    input_names = sorted({
        name
        for trial in trials
        for name, value in trial.get("input_params", {}).items()
        if name not in _SERVICE_INPUT_PARAMS and isinstance(value, (str, int, float, bool))
    })
    headers = ["Run", *(_human_label(name) for name in input_names), "Outputs", "Status", "Duration"]
    lines.extend(("", "## Runs", "", "| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)))
    for trial in trials:
        trial_id = str(trial.get("trial_id") or trial.get("playbook_name") or "Trial")
        notebook_target = trial.get("output_html") or trial.get("output_notebook") or trial.get("output_path")
        run_cell = _markdown_link(trial_id, notebook_target) if notebook_target else trial_id
        values = [run_cell]
        values.extend(_markdown_cell(trial.get("input_params", {}).get(name, "")) for name in input_names)
        output_target = trial.get("output_markdown")
        values.append(_markdown_link("Open outputs", output_target) if output_target else "")
        values.append(_markdown_cell(trial.get("status", "")))
        duration = trial.get("duration_seconds")
        values.append(_format_duration(duration) if isinstance(duration, (int, float)) else "")
        lines.append("| " + " | ".join(values) + " |")
    if not trials:
        lines.append("| _No runs_ |" + " |" * (len(headers) - 1))
    counts = data.get("summary", {}).get("counts", {})
    lines.extend((
        "",
        "## Summary",
        "",
        "[Open consolidated outputs](report.md)",
        "",
        "| Total | Succeeded | Failed | Running |",
        "|---:|---:|---:|---:|",
        f"| {counts.get('total', 0)} | {counts.get('succeeded', 0)} | {counts.get('failed', 0)} | {counts.get('running', 0)} |",
        "",
        "## Run details",
        "",
    ))
    for trial in trials:
        summary = html.escape(_trial_summary_text(trial))
        lines.extend(("<details>", f"<summary>{summary}</summary>", ""))
        lines.append(f"- Playbook: `{trial.get('playbook_name', '')}`")
        if trial.get("output_markdown"):
            lines.append(f"- Outputs: {_markdown_link('open report', trial['output_markdown'])}")
        if trial.get("resolved_params"):
            lines.extend(("", "```json", _display_value(trial["resolved_params"]), "```"))
        lines.extend(("", "</details>", ""))
    errors = [trial for trial in trials if trial.get("status") == "failed"]
    lines.extend(("## Errors", ""))
    if not errors:
        lines.extend(("No errors.", ""))
    else:
        for trial in errors:
            lines.extend((f"### {trial.get('trial_id', 'Trial')}", "", "```json", _display_value(trial.get("error")), "```", ""))
    return "\n".join(lines) + _sampling_markdown(data, domain_sections or {})


def _report_markdown(data: Mapping[str, Any], domain_sections: Mapping[str, str] | None = None) -> str:
    lines = [
        f"# {html.escape(str(data.get('component_name', 'ZEMI')))} outputs",
        "",
        "[Open main job report](main.md)",
        "",
    ]
    trials = data.get("trials") or data.get("playbooks") or []
    if not trials:
        lines.extend(("_No runs._", ""))
    for trial in trials:
        summary = html.escape(_trial_summary_text(trial))
        target = trial.get("output_markdown")
        lines.extend(("<details>", f"<summary>{summary}</summary>", ""))
        if target:
            lines.extend((f"[Open individual output report]({target})", ""))
        lines.extend((_output_markdown_table(trial.get("output_params", {})), "", "</details>", ""))
    return "\n".join(lines) + _sampling_markdown(data, domain_sections or {})


def _sampling_markdown(data, domain_sections):
    lines = []
    for trial in data.get("job_trial", {}).get("playbook_trials", []):
        lines.extend(("", f"## Optimization: {_markdown_cell(trial['playbook_id'])}", "",
                      (_markdown_link("Open detailed SampleTrial report", trial["report_markdown"])
                       if trial.get("report_markdown") else ""), "",
                      "Optimizer:", "", "```json", _display_value(trial.get("optimizer", {})), "```", "",
                      f"Best sample: `{trial['best_sample']}`", "",
                      "Ranking: " + ", ".join(trial["ranking"]), ""))
        for sample in trial["samples"]:
            lines.extend((f"### {sample['sample_trial_id']}", "", f"Status: {sample['status']}", "",
                          "Parameters:", "", "```json", _display_value(sample["params"]), "```", "",
                          f"Score: `{sample.get('score')}`", "",
                          "Metrics:", "", _output_markdown_table(sample["metrics"]), ""))
            if sample["error"]:
                lines.extend(("Error: " + html.escape(sample["error"]), ""))
            lines.extend(("", "<details><summary>All PlaybookRuns and feedback</summary>", "",
                          "```json", _display_value({"runs": sample["runs"], "feedback": sample["feedback"]}), "```", "", "</details>", ""))
        section = domain_sections.get(trial["playbook_id"])
        if section:
            lines.extend(("", section, ""))
    return "\n".join(lines)

def _params_table(params: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = params.get(name, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a table")
    return copy.deepcopy(dict(value))


def _redact_secrets(value: Any, secrets: set[str]) -> Any:
    if isinstance(value, Mapping):
        return {key: _redact_secrets(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_secrets(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [_redact_secrets(item, secrets) for item in value]
    return "***" if secrets and str(value) in secrets else copy.deepcopy(value)


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f} s"
    minutes, remainder = divmod(seconds, 60); return f"{int(minutes)} min {remainder:.2f} s"


def _error_data(error: BaseException) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)}


__all__ = ["ComponentReport", "Module", "Playbook", "ZemiComponent"]
