"""Parameterized execution for ZEMI Component playbooks."""

from __future__ import annotations

import copy
import json
import sys
import time
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from . import env


def _playbook_name(item: object, index: int) -> str:
    if not isinstance(item, Mapping):
        raise ValueError(f"playbooks_params[{index}] must be a table")
    name = item.get("playbook_name")
    if not isinstance(name, str) or not name:
        raise ValueError(
            f"playbooks_params[{index}].playbook_name must be a non-empty string"
        )
    return name


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def _resolve_params_path(value: str | Path, component_root: Path) -> Path:
    params_directory = component_root / "params"
    label = str(value).replace("\\", "/")
    if label.startswith("@comp/"):
        relative = Path(label.removeprefix("@comp/"))
        path = component_root / relative
    else:
        path = Path(value)
        if not path.is_absolute():
            path = params_directory / path
    resolved = path.resolve()
    try:
        resolved.relative_to(params_directory.resolve())
    except ValueError:
        raise ValueError("Parameter file must be inside @comp/params") from None
    if resolved.suffix.lower() != ".toml":
        raise ValueError("Parameter file must have the .toml extension")
    if not resolved.is_file():
        raise FileNotFoundError(f"Parameter file was not found: {resolved}")
    return resolved


def _has_glob(value: str | Path) -> bool:
    return any(character in str(value) for character in "*?[")


def _resolve_params_glob(value: str | Path, component_root: Path) -> list[Path]:
    params_directory = (component_root / "params").resolve()
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

    matches: list[Path] = []
    for path in sorted(
        params_directory.glob(label),
        key=lambda item: item.name.casefold(),
    ):
        resolved = path.resolve()
        try:
            resolved.relative_to(params_directory)
        except ValueError:
            raise ValueError("Parameter glob must stay inside @comp/params") from None
        if resolved.is_file() and resolved.suffix.lower() == ".toml":
            matches.append(resolved)
    if not matches:
        raise FileNotFoundError(f"Parameter glob matched no files: {value}")
    return matches


def _params_candidates(
    component_root: Path,
    params_file: str | Path | Sequence[str | Path] | None,
) -> list[Path]:
    if params_file is None:
        params_directory = component_root / "params"
        return sorted(
            (
                path.resolve()
                for path in params_directory.glob("*.toml")
                if path.is_file()
            ),
            key=lambda path: (
                path.name != "default_params.toml",
                path.name.casefold(),
            ),
        )

    if isinstance(params_file, (str, Path)):
        values = [params_file]
    elif isinstance(params_file, Sequence):
        values = list(params_file)
        if not values:
            raise ValueError("params_file sequence must not be empty")
    else:
        raise TypeError(
            "params_file must be a path, glob, sequence of paths/globs, or None"
        )

    candidates: list[Path] = []
    for value in values:
        if not isinstance(value, (str, Path)):
            raise TypeError("Every params_file item must be a string or Path")
        if _has_glob(value):
            candidates.extend(_resolve_params_glob(value, component_root))
        else:
            candidates.append(_resolve_params_path(value, component_root))

    return list(dict.fromkeys(candidates))


def _select_params_path(
    component_root: Path,
    params_file: str | Path | Sequence[str | Path] | None,
) -> Path:
    candidates = _params_candidates(component_root, params_file)
    if not candidates:
        raise FileNotFoundError("No TOML parameter files were found in @comp/params")
    if len(candidates) == 1:
        return candidates[0]

    print("Available component parameter files:")
    for index, path in enumerate(candidates, start=1):
        print(f"  {index}. {path.name}")
    try:
        choice = input("Select a parameter file by number or name: ").strip()
    except EOFError:
        raise RuntimeError(
            "Multiple parameter files were found. Set params_file explicitly "
            "for non-interactive execution."
        ) from None
    if choice.isdecimal():
        index = int(choice) - 1
        if 0 <= index < len(candidates):
            return candidates[index]
    else:
        for path in candidates:
            if path.name == choice:
                return path
    raise ValueError(f"Invalid parameter file selection: {choice!r}")


class ComponentReport:
    """In-memory component lifecycle report persisted when the component closes."""

    def __init__(
        self,
        component_name: str,
        component_root: Path,
        run_directory: Path,
        params_file: str,
        pipeline_params: Mapping[str, Any],
    ) -> None:
        self.path = run_directory / "report.json"
        self.data: dict[str, Any] = {
            "component_name": component_name,
            "component_root": str(component_root),
            "params_file": params_file,
            "pipeline_params": copy.deepcopy(dict(pipeline_params)),
            "started_at": _timestamp(),
            "status": "running",
            "playbooks": [],
        }

    def start_playbook(self, name: str, output_path: Path) -> dict[str, Any]:
        entry = {
            "playbook_name": name,
            "output_path": str(output_path),
            "started_at": _timestamp(),
            "status": "running",
        }
        self.data["playbooks"].append(entry)
        return entry

    @staticmethod
    def finish_playbook(entry: dict[str, Any]) -> None:
        entry.update(status="succeeded", finished_at=_timestamp())

    @staticmethod
    def fail_playbook(entry: dict[str, Any], error: BaseException) -> None:
        entry.update(
            status="failed",
            finished_at=_timestamp(),
            error=_error_data(error),
        )

    def record_failure(self, error: BaseException) -> None:
        self.data["status"] = "failed"
        self.data["error"] = _error_data(error)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class Playbook:
    """One configured component notebook executable through Papermill."""

    def __init__(
        self,
        component: "ZemiComponent",
        config: Mapping[str, Any],
    ) -> None:
        self.component = component
        self.config = copy.deepcopy(dict(config))
        self.playbook_name = _playbook_name(self.config, 0)
        self.enabled = self.config.get("enabled", True)
        if not isinstance(self.enabled, bool):
            raise ValueError(f"enabled must be boolean for {self.playbook_name!r}")
        params = self.config.get("playbook_params", {})
        if not isinstance(params, Mapping):
            raise ValueError(
                f"playbook_params must be a table for {self.playbook_name!r}"
            )
        self.params = copy.deepcopy(dict(params))
        relative = Path(self.playbook_name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid playbook_name: {self.playbook_name!r}")
        self.source_path = component.root / relative
        if not self.source_path.is_file():
            raise FileNotFoundError(f"Playbook notebook was not found: {self.source_path}")
        self.output_path = component.run_directory / relative

    def run(self) -> None:
        """Execute the source notebook into the component run directory."""
        import papermill

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        self._print_start()
        entry = self.component.report.start_playbook(
            self.playbook_name,
            self.output_path,
        )
        try:
            papermill.execute_notebook(
                str(self.source_path),
                str(self.output_path),
                parameters=copy.deepcopy(self.params),
                cwd=str(self.component.root),
                progress_bar=True,
                log_output=False,
                stdout_file=sys.stdout,
                stderr_file=sys.stderr,
            )
        except Exception as error:
            duration = time.monotonic() - started
            try:
                entry["timed_cells"] = self._add_cell_timings()
            except Exception as timing_error:
                entry["timing_error"] = _error_data(timing_error)
            entry["duration_seconds"] = duration
            self.component.report.fail_playbook(entry, error)
            self._print_failure(error, duration)
            raise
        try:
            entry["timed_cells"] = self._add_cell_timings()
        except Exception as error:
            duration = time.monotonic() - started
            entry["duration_seconds"] = duration
            self.component.report.fail_playbook(entry, error)
            self._print_failure(error, duration)
            raise
        duration = time.monotonic() - started
        entry["duration_seconds"] = duration
        self.component.report.finish_playbook(entry)
        self._print_success(duration)

    def _print_start(self) -> None:
        line = "═" * 78
        print()
        print(line)
        print("ZEMI COMPONENT · PLAYBOOK START")
        print(f"Component : {self.component.name}")
        print(f"Playbook  : {self.playbook_name}")
        print(
            "Parameters: "
            f"{self.component.params_path.relative_to(self.component.root).as_posix()}"
        )
        print(
            "Output    : "
            f"{self.output_path.relative_to(self.component.root).as_posix()}"
        )
        print(line)

    def _print_success(self, duration: float) -> None:
        line = "═" * 78
        print(line)
        print(f"✓ PLAYBOOK COMPLETED · {self.playbook_name}")
        print(f"  Duration: {_format_duration(duration)}")
        print(f"  Output  : {self.output_path.relative_to(self.component.root).as_posix()}")
        print(line)

    def _print_failure(self, error: BaseException, duration: float) -> None:
        line = "!" * 78
        print(line)
        print(f"✗ PLAYBOOK FAILED · {self.playbook_name}")
        print(f"  Duration: {_format_duration(duration)}")
        print(f"  Error   : {type(error).__name__}: {error}")
        print(f"  Output  : {self.output_path.relative_to(self.component.root).as_posix()}")
        print(line)

    def _add_cell_timings(self) -> int:
        """Add visible timing notes to executed code cells in the run notebook."""
        if not self.output_path.is_file():
            return 0

        import nbformat

        notebook = nbformat.read(self.output_path, as_version=4)
        cells = []
        timed_cells = 0
        for cell in notebook.cells:
            if "zemi-cell-timing" in cell.metadata.get("tags", []):
                continue
            cells.append(cell)
            if cell.cell_type != "code":
                continue
            duration = cell.metadata.get("papermill", {}).get("duration")
            if duration is None:
                continue
            try:
                duration_seconds = float(duration)
            except (TypeError, ValueError):
                continue
            note = nbformat.v4.new_markdown_cell(
                f"> ⏱ Время выполнения ячейки: **{duration_seconds:.3f} с**"
            )
            note.metadata["tags"] = ["zemi-cell-timing"]
            note.metadata["zemi"] = {
                "source_cell_id": cell.get("id"),
                "duration_seconds": duration_seconds,
            }
            cells.append(note)
            timed_cells += 1
        notebook.cells = cells
        nbformat.write(notebook, self.output_path)
        return timed_cells


class ZemiComponent:
    """Load component parameters and own playbook execution lifecycle."""

    def __init__(
        self,
        params_file: str | Path | Sequence[str | Path] | None = None,
    ) -> None:
        self.root = env.path.comp.root
        self.params_path = _select_params_path(self.root, params_file)
        self.params = _load_toml(self.params_path)
        self.pipeline_params = _params_table(self.params, "pipeline_params")
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
        self.report = ComponentReport(
            self.name,
            self.root,
            self.run_directory,
            self.params_path.relative_to(self.root).as_posix(),
            self.pipeline_params,
        )
        playbook_configs = self.params.get("playbooks_params", [])
        if not isinstance(playbook_configs, list):
            raise ValueError("playbooks_params must be an array of tables")
        names = [_playbook_name(item, index) for index, item in enumerate(playbook_configs)]
        if len(names) != len(set(names)):
            raise ValueError("playbook_name values must be unique")
        self.playbooks = tuple(Playbook(self, item) for item in playbook_configs)
        self._closed = False

    def close(self) -> None:
        """Finish and persist the component report; safe to call more than once."""
        if self._closed:
            return
        if self.report.data["status"] == "running":
            self.report.data["status"] = "succeeded"
        self.report.data["finished_at"] = _timestamp()
        self.report.save()
        self._closed = True


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
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes)} min {remainder:.2f} s"


def _error_data(error: BaseException) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)}


__all__ = [
    "ComponentReport",
    "Playbook",
    "ZemiComponent",
]
