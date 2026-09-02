"""Public notebook helpers for structured ZEMI playbook output."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

PLAYBOOK_OUTPUT_MIME = "application/vnd.zemi.playbook-output+json"
_OUTPUT_DIR_ENV = "ZEMI_PLAYBOOK_OUTPUT_DIR"
_published = False


@contextmanager
def _output_context(directory: Path) -> Iterator[None]:
    """Expose the runner-owned output directory to the notebook process."""
    previous = os.environ.get(_OUTPUT_DIR_ENV)
    os.environ[_OUTPUT_DIR_ENV] = str(directory.resolve())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_OUTPUT_DIR_ENV, None)
        else:
            os.environ[_OUTPUT_DIR_ENV] = previous


def output_dir() -> Path:
    """Return the absolute output directory for the active component run."""
    configured = os.environ.get(_OUTPUT_DIR_ENV)
    if configured is None:
        raise RuntimeError(
            "playbook.output_dir() requires an active ZEMI playbook run"
        )
    directory = Path(configured)
    if not directory.is_absolute():
        raise RuntimeError(
            "playbook.output_dir() received an invalid non-absolute run path"
        )
    directory.mkdir(parents=True, exist_ok=True)
    return directory.resolve()


def output_path(relative_path: str | os.PathLike[str]) -> Path:
    """Return a safe path below the active run directory and create its parent."""
    try:
        relative = Path(relative_path)
    except TypeError as error:
        raise TypeError(
            "playbook.output_path() requires a str or os.PathLike path"
        ) from error
    if relative.is_absolute():
        raise ValueError("playbook.output_path() requires a relative path")
    if ".." in relative.parts:
        raise ValueError("playbook.output_path() path must not contain '..'")

    directory = output_dir()
    candidate = (directory / relative).resolve()
    try:
        candidate.relative_to(directory)
    except ValueError as error:
        raise ValueError(
            "playbook.output_path() path must remain inside the run directory"
        ) from error
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def validate_output_params(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("output_params() requires a mapping")
    if not all(isinstance(key, str) for key in value):
        raise TypeError("output_params() mapping keys must be strings")
    try:
        return json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"output_params() requires finite JSON-serializable values: {error}") from error


def output_params(value: Mapping[str, Any]) -> None:
    """Publish one structured mapping with a standard notebook fallback."""
    global _published
    if _published:
        raise RuntimeError("output_params() may be called only once per notebook execution")
    normalized = validate_output_params(value)
    from IPython.display import display
    display(
        {
            PLAYBOOK_OUTPUT_MIME: normalized,
            "text/plain": json.dumps(normalized, ensure_ascii=False, indent=2),
        },
        raw=True,
    )
    _published = True


__all__ = ["PLAYBOOK_OUTPUT_MIME", "output_dir", "output_params", "output_path"]
