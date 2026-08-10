"""Чтение и общая валидация TOML-конфигураций ZEMI.

Модуль сохраняет стандартное дерево ``dict``/``list`` из :mod:`tomllib` и не
создаёт предметные объекты. Дополнительная обработка ограничена проверкой:

* непустые значения ``name`` в одном массиве таблиц должны быть уникальными;
* строки с префиксом ``@inst/`` или ``@comp/`` должны указывать на существующий
  путь внутри соответствующего корня ZEMI.

ZEMI-ссылки остаются исходными строками. Чтение содержимого связанных файлов —
ответственность использующего конфигурацию модуля.
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
    """Проверяет существование ZEMI-пути, не изменяя исходную строку."""
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
            raise ValueError(f"Некорректный ZEMI-путь в {location}: {value!r}")

        path = getattr(env.path, root_name) / relative_path
        if not path.exists():
            raise FileNotFoundError(
                f"ZEMI-путь из {location} не существует: {value!r} ({path})"
            )
        return


def _validate(value: Any, location: str = "root") -> None:
    """Рекурсивно проверяет ссылки и уникальность имён в обычном TOML-дереве."""
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
                raise ValueError(f"{location}[{index}].name должен быть строкой")
            if name in names:
                raise ValueError(
                    f"повторяющееся имя {name!r} в массиве {location}"
                )
            names.add(name)

    for index, item in enumerate(value):
        _validate(item, f"{location}[{index}]")


def load(path: str | Path) -> dict[str, Any]:
    """Читает TOML и валидирует общие ограничения ZEMI без изменения данных."""
    with Path(path).open("rb") as file:
        data = tomllib.load(file)
    _validate(data)
    return data


__all__ = ["load"]
