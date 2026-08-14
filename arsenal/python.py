"""Создание стандартных и компонентных Python venv ZEMI Arsenal."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib


INSTANCE_MARKERS = (".zemiinst_dev", ".zemiinst_exp", ".zemiinst_prod")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "zemi_python_venv.toml"

PACKAGES = (
    "openai>=1.30.0",
    "httpx>=0.27.0",
    "pydantic>=2.7.0",
    "starlette==0.46.2",
    "cryptography==44.0.0",
    "joserfc==1.1.0",
    "python-calamine>=0.2.0",
    "openpyxl>=3.1.0",
    "markitdown>=0.0.1a0",
    "pandas>=2.2.0",
    "duckdb>=1.0.0",
    "fastembed>=0.3.0",
    "streamlit>=1.35.0",
    "dspy-ai>=2.4.0",
    "instructor>=1.3.0",
    "pydantic-ai>=0.0.14",
    "baml-py>=0.70.0",
    "smolagents>=1.0.0",
    "litellm>=1.35.0",
    "outlines>=0.0.40",
    "guidance>=0.1.15",
    "llama-index-core>=0.10.0",
    "llama-index-llms-openai>=0.1.0",
    "llama-index-llms-openai-like",
    "unstructured-client>=0.25.0",
    "ipykernel",
)

IMPORTS = (
    "python_calamine",
    "openpyxl",
    "markitdown",
    "pandas",
    "duckdb",
    "fastembed",
    "streamlit",
    "dspy",
    "instructor",
    "pydantic_ai",
    "baml_py",
    "smolagents",
    "litellm",
    "outlines",
    "guidance",
    "llama_index.core",
    "llama_index.llms.openai_like",
    "unstructured_client",
    "llama_cpp_agent",
    "ipykernel",
)

LLAMA_CPP_STUB = '''\
"""ZEMI REST-mode compatibility stub for external llama-server.exe."""
from unittest.mock import MagicMock

def __getattr__(name: str):
    return MagicMock(name=name)

Llama = MagicMock
LlamaGrammar = MagicMock
'''

_BANNER_WIDTH = 78


def _step(number: int, title: str, *details: str) -> None:
    print()
    print("═" * _BANNER_WIDTH)
    print(f"ZEMI Python venv · [{number}] {title}")
    print("─" * _BANNER_WIDTH)
    for detail in details:
        print(detail)
    print("═" * _BANNER_WIDTH)


def _step_done(number: int, message: str) -> None:
    print()
    print("─" * _BANNER_WIDTH)
    print(f"✓ [{number}] {message}")
    print("─" * _BANNER_WIDTH)


@dataclass(frozen=True)
class _PythonVenvPaths:
    component_root: Path
    instance_root: Path
    base_python: Path
    environment_root: Path
    python: Path
    settings_path: Path


@dataclass(frozen=True)
class _PythonVenvConfig:
    winpython_version: str
    zemi_venv_version: str


def _config(path: str | Path = CONFIG_PATH) -> _PythonVenvConfig:
    """Загружает версии базового WinPython и среды ZEMI из TOML."""
    config_path = Path(path)
    with config_path.open("rb") as file:
        values = tomllib.load(file)
    required = ("winpython_version", "zemi_venv_version")
    for name in required:
        value = values.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} должен быть непустой строкой: {config_path}")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError(f"Некорректный {name}: {value!r}")
    return _PythonVenvConfig(
        winpython_version=values["winpython_version"],
        zemi_venv_version=values["zemi_venv_version"],
    )


def _find_root(start: Path, marker_names: tuple[str, ...]) -> Path:
    start = start.resolve()
    directory = start if start.is_dir() else start.parent
    for candidate in (directory, *directory.parents):
        markers = [name for name in marker_names if (candidate / name).is_file()]
        if len(markers) == 1:
            return candidate
        if len(markers) > 1:
            raise RuntimeError(
                f"В {candidate} найдено несколько маркеров: {', '.join(markers)}"
            )
    raise FileNotFoundError(f"Не найден корень с маркером: {', '.join(marker_names)}")


def _environment_paths(
    start: str | Path | None = None,
    *,
    component_name: str | None = None,
    component_version: str | None = None,
) -> _PythonVenvPaths:
    """Вычисляет пути стандартного или компонентного venv."""
    if (component_name is None) != (component_version is None):
        raise ValueError(
            "component_name и component_version должны быть указаны вместе"
        )
    if component_name is not None and not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?", component_name
    ):
        raise ValueError(
            "component_name должен быть коротким именем из строчных букв, "
            f"цифр, '.', '_' и '-': {component_name!r}"
        )
    if component_version is not None and not re.fullmatch(
        r"[0-9]{6}", component_version
    ):
        raise ValueError(
            "version должен иметь формат YYMMDD, например '260814': "
            f"{component_version!r}"
        )

    origin = Path.cwd() if start is None else Path(start)
    component_root = _find_root(origin, (".zemicomp",))
    instance_root = _find_root(component_root, INSTANCE_MARKERS)
    versions = _config()
    base_python = (
        instance_root
        / "_pythons"
        / versions.winpython_version
        / "python"
        / "python.exe"
    )
    name_parts = [versions.zemi_venv_version, versions.winpython_version]
    if component_name is not None and component_version is not None:
        name_parts[:0] = [component_name, component_version]
    environment_root = instance_root / "_venvs" / "-".join(name_parts)
    return _PythonVenvPaths(
        component_root=component_root,
        instance_root=instance_root,
        base_python=base_python,
        environment_root=environment_root,
        python=environment_root / "Scripts" / "python.exe",
        settings_path=component_root / ".vscode" / "settings.json",
    )


def _create_if_missing(paths: _PythonVenvPaths) -> bool:
    """Создаёт наследующую WinPython среду или проверяет существующую."""
    if not paths.base_python.is_file():
        raise FileNotFoundError(f"Не найден базовый WinPython: {paths.base_python}")
    created = not paths.python.is_file()
    if not paths.python.is_file():
        paths.environment_root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                str(paths.base_python),
                "-m",
                "venv",
                "--system-site-packages",
                str(paths.environment_root),
            ],
            cwd=paths.component_root,
            check=True,
        )
    _verify(paths)
    return created


def _verify(paths: _PythonVenvPaths) -> None:
    """Проверяет базовый Python и наследование его пакетов."""
    if not paths.python.is_file():
        raise FileNotFoundError(f"Не найден Python среды: {paths.python}")
    config_path = paths.environment_root / "pyvenv.cfg"
    config = config_path.read_text(encoding="utf-8").lower().replace(" ", "")
    if "include-system-site-packages=true" not in config:
        raise RuntimeError(f"Среда не наследует пакеты WinPython: {config_path}")
    result = subprocess.run(
        [str(paths.python), "-c", "import sys; print(sys.base_prefix)"],
        cwd=paths.component_root,
        check=True,
        text=True,
        capture_output=True,
    )
    expected = paths.base_python.parent.resolve()
    actual = Path(result.stdout.strip()).resolve()
    if actual != expected:
        raise RuntimeError(
            f"Среда основана на другом Python: {actual}; ожидался {expected}"
        )


def _install_zemi_packages(paths: _PythonVenvPaths) -> None:
    """Устанавливает зависимости Arsenal в созданную среду."""
    if not paths.python.is_file():
        raise FileNotFoundError(
            "Python venv ещё не создан. Сначала вызовите create_if_missing(): "
            f"{paths.python}"
        )
    subprocess.run(
        [str(paths.python), "-m", "pip", "install", "--only-binary", ":all:", *PACKAGES],
        cwd=paths.component_root,
        check=True,
    )
    subprocess.run(
        [str(paths.python), "-m", "pip", "install", "--no-deps", "llama-cpp-agent>=0.2.0"],
        cwd=paths.component_root,
        check=True,
    )

    purelib = subprocess.run(
        [str(paths.python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        cwd=paths.component_root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    (Path(purelib) / "llama_cpp.py").write_text(LLAMA_CPP_STUB, encoding="utf-8")

    script = (
        "import importlib, os; "
        "os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'; "
        f"[importlib.import_module(name) for name in {IMPORTS!r}]"
    )
    subprocess.run(
        [str(paths.python), "-c", script],
        cwd=paths.component_root,
        check=True,
    )


def _set_as_vscode_interpreter(paths: _PythonVenvPaths) -> Path:
    """Сохраняет среду как default Python, не затирая другие настройки VS Code."""
    settings: dict[str, object] = {}
    if paths.settings_path.is_file():
        loaded = json.loads(paths.settings_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Настройки VS Code должны быть объектом: {paths.settings_path}")
        settings.update(loaded)

    relative = os.path.relpath(paths.python, paths.component_root).replace("\\", "/")
    settings["python.defaultInterpreterPath"] = f"${{workspaceFolder}}/{relative}"
    paths.settings_path.parent.mkdir(parents=True, exist_ok=True)
    paths.settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    return paths.settings_path


class PythonVenv:
    """Пошаговая установка стандартного или компонентного Python venv."""

    def __init__(self, paths: _PythonVenvPaths, *, component_specific: bool) -> None:
        self._paths = paths
        self._component_specific = component_specific
        self._step_number = 0

    @classmethod
    def standard(cls, start: str | Path | None = None) -> PythonVenv:
        """Возвращает стандартный общий venv ZEMI."""
        return cls(_environment_paths(start), component_specific=False)

    @classmethod
    def for_component(
        cls,
        *,
        component_name: str,
        version: str,
        start: str | Path | None = None,
    ) -> PythonVenv:
        """Возвращает отдельный версионированный venv компонента."""
        return cls(
            _environment_paths(
                start,
                component_name=component_name,
                component_version=version,
            ),
            component_specific=True,
        )

    @property
    def python(self) -> Path:
        return self._paths.python

    @property
    def root(self) -> Path:
        return self._paths.environment_root

    def _begin(self, title: str, *details: str) -> int:
        self._step_number += 1
        _step(self._step_number, title, *details)
        return self._step_number

    def create_if_missing(self) -> None:
        """Создаёт venv при отсутствии и проверяет его основу."""
        number = self._begin(
            "СОЗДАНИЕ VENV",
            f"Имя:      {self.root.name}",
            f"Путь:     {self.root}",
            f"WinPython: {self._paths.base_python.parent}",
        )
        created = _create_if_missing(self._paths)
        message = "Python venv создан" if created else "Python venv уже существует"
        _step_done(number, message)

    def install_zemi_packages(self) -> None:
        """Устанавливает и проверяет стандартные пакеты ZEMI."""
        number = self._begin(
            "ПАКЕТЫ ZEMI",
            f"Устанавливаю и проверяю {len(PACKAGES) + 1} пакетов.",
            "Сообщения pip 'Attempting uninstall' ожидаемы для наследуемого WinPython.",
        )
        _install_zemi_packages(self._paths)
        _step_done(number, "Пакеты ZEMI установлены и проверены")

    def install_packages(self, *packages: str) -> None:
        """Устанавливает пользовательские пакеты в компонентный venv."""
        number = self._begin("ПАКЕТЫ КОМПОНЕНТА")
        if not packages:
            _step_done(number, "Дополнительные пакеты не указаны — этап пропущен")
            return
        if not self._component_specific:
            raise RuntimeError(
                "Пакеты компонента нельзя устанавливать в стандартный общий venv. "
                "Используйте PythonVenv.for_component(component_name=..., version=...)."
            )
        subprocess.run(
            [str(self.python), "-m", "pip", "install", *packages],
            cwd=self._paths.component_root,
            check=True,
        )
        _step_done(number, f"Установлено пакетов компонента: {len(packages)}")

    def run_script(self, script: str, *arguments: str) -> None:
        """Запускает установочный Python-скрипт в компонентном venv."""
        if not self._component_specific:
            raise RuntimeError(
                "Установочный код компонента нельзя запускать для стандартного общего "
                "venv. Используйте PythonVenv.for_component(...)."
            )
        prefixes = {
            "@comp/": self._paths.component_root,
            "@inst/": self._paths.instance_root,
        }
        script_path: Path | None = None
        for prefix, root in prefixes.items():
            if script.startswith(prefix):
                script_path = root / Path(script.removeprefix(prefix))
                break
        if script_path is None:
            raise ValueError("Путь script должен начинаться с @comp/ или @inst/")
        if not script_path.is_file():
            raise FileNotFoundError(f"Не найден установочный скрипт: {script_path}")

        number = self._begin("УСТАНОВОЧНЫЙ КОД КОМПОНЕНТА", f"Скрипт: {script}")
        subprocess.run(
            [str(self.python), str(script_path), *arguments],
            cwd=self._paths.component_root,
            check=True,
        )
        _step_done(number, "Установочный скрипт выполнен")

    def verify(self) -> None:
        """Проверяет Python venv и его связь с нужным WinPython."""
        number = self._begin(
            "ПРОВЕРКА VENV",
            f"Python: {self.python}",
        )
        _verify(self._paths)
        _step_done(number, "Python venv исправен")

    def set_as_vscode_interpreter(self) -> None:
        """Назначает Python venv интерпретатором текущего проекта VS Code."""
        number = self._begin(
            "ИНТЕРПРЕТАТОР VS CODE",
            f"Python: {self.python}",
        )
        settings_path = _set_as_vscode_interpreter(self._paths)
        _step_done(number, f"VS Code настроен: {settings_path}")


__all__ = [
    "IMPORTS",
    "PACKAGES",
    "PythonVenv",
]
