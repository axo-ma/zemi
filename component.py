"""Parameterized execution and reporting for ZEMI Component playbooks."""

from __future__ import annotations

import copy
import itertools
import json
import os
import re
import sys
import time
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from . import env
from .playbook import PLAYBOOK_OUTPUT_MIME, validate_output_params


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

    _WRAPPER_KEYS = {"ref", "each", "select"}

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
        wrapper_keys = set(value) & {"each", "select"} if isinstance(value, Mapping) else set()
        if wrapper_keys:
            if len(wrapper_keys) != 1 or set(value) != wrapper_keys:
                mode = "each/select" if len(wrapper_keys) > 1 else next(iter(wrapper_keys))
                raise ValueError(f"{label}.{name}: {mode} wrapper must contain exactly one mode key")
            mode = next(iter(wrapper_keys))
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
            selected = copy.deepcopy(normalized[int(answer) - 1])
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
    """Canonical JSON report and self-contained offline HTML rendering."""

    def __init__(self, component_name: str, component_root: Path, run_directory: Path, params_file: str, pipeline_params: Mapping[str, Any]) -> None:
        self.path = run_directory / "report.json"
        self.html_path = run_directory / "report.html"
        trials: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {"schema_version": 1, "component_name": component_name, "component_root": str(component_root), "params_file": params_file, "pipeline_params": copy.deepcopy(dict(pipeline_params)), "started_at": _timestamp(), "finished_at": None, "status": "running", "trials": trials, "playbooks": trials, "summary": {}}

    def start_trial(self, playbook: "Playbook") -> dict[str, Any]:
        entry = {"trial_id": playbook.trial_id, "playbook_name": playbook.playbook_name, "input_params": copy.deepcopy(playbook.params), "resolved_params": copy.deepcopy(playbook.resolved_params), "output_params": {}, "output_notebook": playbook.output_relative.as_posix(), "output_path": playbook.output_relative.as_posix(), "started_at": _timestamp(), "finished_at": None, "duration_seconds": None, "status": "running", "error": None}
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

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["summary"] = _summarize_trials(self.data["trials"])
        json_tmp = self.path.with_name(".report.json.tmp")
        json_tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(json_tmp, self.path)
        html_tmp = self.html_path.with_name(".report.html.tmp")
        html_tmp.write_text(_report_html(self.data), encoding="utf-8")
        os.replace(html_tmp, self.html_path)


class Playbook:
    """One expanded notebook trial executable through Papermill."""

    def __init__(self, component: "ZemiComponent", config: Mapping[str, Any], *, config_index: int = 0, trial_index: int = 0, params: Mapping[str, Any] | None = None, resolved_params: Mapping[str, Any] | None = None) -> None:
        self.component = component; self.config = copy.deepcopy(dict(config))
        self.playbook_name = _playbook_name(config, config_index)
        self.enabled = config.get("enabled", True)
        if not isinstance(self.enabled, bool):
            raise ValueError(f"enabled must be boolean for {self.playbook_name!r}")
        configured = config.get("playbook_params", {}) if params is None else params
        if not isinstance(configured, Mapping):
            raise ValueError(f"playbook_params must be a table for {self.playbook_name!r}")
        self.params = copy.deepcopy(dict(configured))
        self.resolved_params = copy.deepcopy(dict(resolved_params or {}))
        relative = Path(self.playbook_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid playbook_name: {self.playbook_name!r}")
        self.source_path = component.root / relative
        if not self.source_path.is_file():
            raise FileNotFoundError(f"Playbook notebook was not found: {self.source_path}")
        self.trial_id = _trial_id(self.playbook_name, config_index, trial_index)
        self.output_relative = Path("notebooks") / f"{self.trial_id}.ipynb"
        self.output_path = component.run_directory / self.output_relative

    def run(self) -> None:
        import papermill
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic(); self._print_start(); entry = self.component.report.start_trial(self)
        try:
            papermill.execute_notebook(str(self.source_path), str(self.output_path), parameters=copy.deepcopy(self.params), cwd=str(self.component.root), progress_bar=True, log_output=False, stdout_file=sys.stdout, stderr_file=sys.stderr)
            entry["output_params"] = self._extract_output_params()
            entry["timed_cells"] = self._add_cell_timings()
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


class ZemiComponent:
    """Load component parameters and own expanded playbook trial lifecycle."""

    def __init__(self, params_file: str | Path | Sequence[str | Path] | None = None) -> None:
        self.root = env.path.comp.root; self.params_path = _select_params_path(self.root, params_file)
        with self.params_path.open("rb") as file:
            self.params = tomllib.load(file)
        reference_resolver = _ParamReferenceResolver(self.params)
        self.pipeline_params = _params_table(self.params, "pipeline_params"); self.component_params = _params_table(self.params, "component_params")
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
            name = group.get("name")
            if not isinstance(name, str) or not name:
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
                if group.get("_legacy"):
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
                for trial_index, (params, resolved) in enumerate(_resolve_playbook_params(raw, label, config["playbook_name"], reference_origins)):
                    playbook = Playbook(self, config, config_index=config_index, trial_index=trial_index, params=params, resolved_params=resolved)
                    playbooks.append(playbook); group_playbooks.append(playbook)
                config_index += 1
            self._arsenal_groups.append((managed, arsenal_config_path, tuple(group_playbooks)))
        self.playbooks = tuple(playbooks); self._closed = False; self.report.save()

    def run(self) -> None:
        first_error: BaseException | None = None
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
                if any(trial.get("status") == "failed" for trial in self.report.data["trials"])
                else "succeeded"
            )
        self.report.data["finished_at"] = _timestamp()
        self.report.save()
        marker_tmp = self.run_directory / ".complete.tmp"
        marker_tmp.write_bytes(b"")
        os.replace(marker_tmp, self.run_directory / "complete")
        self._closed = True


def _report_html(data: Mapping[str, Any]) -> str:
    embedded = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ZEMI job report</title><style>
:root{{--bg:#f6f7fb;--card:#fff;--ink:#172033;--muted:#667085;--line:#d9deea}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}}main{{max-width:1500px;margin:auto;padding:24px}}section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin:16px 0;overflow:auto}}h1,h2{{margin:0 0 14px}}.controls{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}}input,select{{padding:8px;border:1px solid var(--line);border-radius:6px}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}}th{{cursor:pointer}}pre{{white-space:pre-wrap;word-break:break-word}}</style></head><body><main><h1>ZEMI job report</h1>
<section id="overview"><h2>Overview</h2><div></div></section>
<section id="runs"><h2>Runs</h2><div class="controls"><input id="search" placeholder="Search all values"><select id="playbook-filter"><option value="">All playbooks</option></select><select id="status-filter"><option value="">All statuses</option></select><select id="parameter-filter"><option value="">Any parameter</option></select><input id="parameter-value" placeholder="Parameter value contains"></div><div></div></section>
<section id="summary"><h2>Summary</h2><div></div></section>
<section id="run-details"><h2>Run details</h2><div></div></section><section id="errors"><h2>Errors</h2><div></div></section>
<script id="report-data" type="application/json">{embedded}</script><script>
const report=JSON.parse(document.getElementById('report-data').textContent),trials=report.trials||report.playbooks||[];const text=(tag,v)=>{{const n=document.createElement(tag);n.textContent=v;return n}},json=v=>JSON.stringify(v,null,2),scalar=v=>v===null||['string','number','boolean'].includes(typeof v);
const counts={{total:trials.length,succeeded:trials.filter(x=>x.status==='succeeded').length,failed:trials.filter(x=>x.status==='failed').length}};document.querySelector('#overview div').append(text('pre',json({{component_name:report.component_name,params_file:report.params_file,status:report.status,started_at:report.started_at,finished_at:report.finished_at,counts}})));document.querySelector('#summary div').append(text('pre',json(report.summary||[])));
const ins=[...new Set(trials.flatMap(t=>Object.keys(t.input_params||{{}}).filter(k=>scalar(t.input_params[k]))))].sort(),outs=[...new Set(trials.flatMap(t=>Object.keys(t.output_params||{{}}).filter(k=>scalar(t.output_params[k]))))].sort(),cols=['trial_id','playbook_name',...ins.map(k=>'in:'+k),...outs.map(k=>'out:'+k),'status','duration_seconds','output_notebook'];let sort='trial_id',asc=true;const val=(t,k)=>k.startsWith('in:')?(t.input_params||{{}})[k.slice(3)]:k.startsWith('out:')?(t.output_params||{{}})[k.slice(4)]:t[k];
function render(){{let q=document.getElementById('search').value.toLowerCase(),p=document.getElementById('playbook-filter').value,s=document.getElementById('status-filter').value,pk=document.getElementById('parameter-filter').value,pv=document.getElementById('parameter-value').value.toLowerCase(),rows=trials.filter(t=>(!p||t.playbook_name===p)&&(!s||t.status===s)&&(!q||json(t).toLowerCase().includes(q))&&(!pk||!pv||String(val(t,pk)??'').toLowerCase().includes(pv)));rows.sort((a,b)=>String(val(a,sort)??'').localeCompare(String(val(b,sort)??''),undefined,{{numeric:true}})*(asc?1:-1));const table=document.createElement('table'),head=document.createElement('tr');cols.forEach(k=>{{const th=text('th',k);th.onclick=()=>{{asc=sort===k?!asc:true;sort=k;render()}};head.append(th)}});table.append(head);rows.forEach(t=>{{const tr=document.createElement('tr');cols.forEach(k=>{{const td=document.createElement('td'),v=val(t,k);if(k==='output_notebook'&&v){{const a=text('a',v);a.href=v;td.append(a)}}else td.textContent=v??'';tr.append(td)}});table.append(tr)}});document.querySelector('#runs div:last-child').replaceChildren(table)}}
for(const p of [...new Set(trials.map(t=>t.playbook_name))].sort()){{const o=text('option',p);o.value=p;document.getElementById('playbook-filter').append(o)}}for(const s of [...new Set(trials.map(t=>t.status))].sort()){{const o=text('option',s);o.value=s;document.getElementById('status-filter').append(o)}}for(const k of [...ins.map(k=>'in:'+k),...outs.map(k=>'out:'+k)]){{const o=text('option',k);o.value=k;document.getElementById('parameter-filter').append(o)}}['search','playbook-filter','status-filter','parameter-filter','parameter-value'].forEach(id=>document.getElementById(id).addEventListener('input',render));render();
const details=document.querySelector('#run-details div');trials.forEach(t=>{{const block=document.createElement('article');block.append(text('h3',t.trial_id||'Trial'));block.append(text('pre',json({{playbook_name:t.playbook_name,status:t.status,input_params:t.input_params||{{}},output_params:t.output_params||{{}},error:t.error||null}})));details.append(block)}});const errors=trials.filter(t=>t.status==='failed');document.querySelector('#errors div').append(text(errors.length?'pre':'p',errors.length?json(errors.map(t=>({{trial_id:t.trial_id,error:t.error}}))):'No errors.'));
</script></main></body></html>'''


def _params_table(params: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = params.get(name, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a table")
    return copy.deepcopy(dict(value))


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f} s"
    minutes, remainder = divmod(seconds, 60); return f"{int(minutes)} min {remainder:.2f} s"


def _error_data(error: BaseException) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)}


__all__ = ["ComponentReport", "Playbook", "ZemiComponent"]
