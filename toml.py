"""Read and perform general validation of ZEMI TOML configurations.

The module preserves the standard ``dict``/``list`` tree from :mod:`tomllib`
and does not create domain objects. Additional processing only validates that:

* non-empty ``name`` values in one array of tables are unique;
* strings prefixed with ``@inst/`` or ``@comp/`` point to an existing path
  inside the corresponding ZEMI root.

ZEMI references remain unchanged strings. The module consuming the
configuration is responsible for reading referenced file contents.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from . import env


_PATH_PREFIXES = {
    "@inst/": "inst",
    "@comp/": "comp",
}


def _validate_reference(value: str, location: str) -> None:
    """Validate that a ZEMI path exists without changing the source string."""
    for prefix, root_name in _PATH_PREFIXES.items():
        if not value.startswith(prefix):
            continue

        relative_value = value.removeprefix(prefix).replace("\\", "/")
        relative_path = Path(relative_value)
        if (
            not relative_value
            or relative_path.is_absolute()
            or ".." in relative_path.parts
        ):
            raise ValueError(f"Invalid ZEMI path at {location}: {value!r}")

        path = getattr(env.path, root_name) / relative_path
        if not path.exists():
            raise FileNotFoundError(
                f"ZEMI path at {location} does not exist: {value!r} ({path})"
            )
        return


def _validate(value: Any, location: str = "root") -> None:
    """Recursively validate references and unique names in a plain TOML tree."""
    if isinstance(value, str):
        _validate_reference(value, location)
        return

    if isinstance(value, dict):
        for key, item in value.items():
            _validate(item, f"{location}.{key}")
        return

    if not isinstance(value, list):
        return

    if value and all(isinstance(item, dict) for item in value):
        names: set[str] = set()
        for index, item in enumerate(value):
            name = item.get("name")
            if name is None or name == "":
                continue
            if not isinstance(name, str):
                raise ValueError(f"{location}[{index}].name must be a string")
            if name in names:
                raise ValueError(
                    f"duplicate name {name!r} in array {location}"
                )
            names.add(name)

    for index, item in enumerate(value):
        _validate(item, f"{location}[{index}]")


def load(path: str | Path) -> dict[str, Any]:
    """Read TOML and validate general ZEMI constraints without changing data."""
    with Path(path).open("rb") as file:
        data = tomllib.load(file)
    _validate(data)
    return data


__all__ = ["load"]
