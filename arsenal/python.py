"""Создание общей версионированной Python-среды ZEMI Arsenal."""

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
    print(f"ZEMI Arsenal Python · [{number}/3] {title}")
    print("─" * _BANNER_WIDTH)
    for detail in details:
        print(detail)
    print("═" * _BANNER_WIDTH)


def _step_done(number: int, message: str) -> None:
    print()
    print("─" * _BANNER_WIDTH)
    print(f"✓ [{number}/3] {message}")
    print("─" * _BANNER_WIDTH)


@dataclass(frozen=True)
class PythonEnvironment:
    component_root: Path
    instance_root: Path
    base_python: Path
    environment_root: Path
    python: Path
    settings_path: Path


@dataclass(frozen=True)
class PythonEnvironmentConfig:
    winpython_version: str
    zemi_venv_version: str


def config(path: str | Path = CONFIG_PATH) -> PythonEnvironmentConfig:
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
    return PythonEnvironmentConfig(
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


def environment(start: str | Path | None = None) -> PythonEnvironment:
    """Вычисляет все пути среды, не изменяя файловую систему."""
    origin = Path.cwd() if start is None else Path(start)
    component_root = _find_root(origin, (".zemicomp",))
    instance_root = _find_root(component_root, INSTANCE_MARKERS)
    versions = config()
    base_python = (
        instance_root
        / "_pythons"
        / versions.winpython_version
        / "python"
        / "python.exe"
    )
    environment_root = (
        instance_root
        / "_venvs"
        / f"{versions.zemi_venv_version}-{versions.winpython_version}"
    )
    return PythonEnvironment(
        component_root=component_root,
        instance_root=instance_root,
        base_python=base_python,
        environment_root=environment_root,
        python=environment_root / "Scripts" / "python.exe",
        settings_path=component_root / ".vscode" / "settings.json",
    )


def create(paths: PythonEnvironment | None = None) -> PythonEnvironment:
    """Создаёт наследующую WinPython среду или проверяет существующую."""
    paths = environment() if paths is None else paths
    if not paths.base_python.is_file():
        raise FileNotFoundError(f"Не найден базовый WinPython: {paths.base_python}")
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
    check(paths)
    return paths


def check(paths: PythonEnvironment | None = None) -> None:
    """Проверяет базовый Python и наследование его пакетов."""
    paths = environment() if paths is None else paths
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


def install(paths: PythonEnvironment | None = None) -> None:
    """Устанавливает зависимости Arsenal в созданную среду."""
    paths = create(paths)
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


def configure_vscode(paths: PythonEnvironment | None = None) -> Path:
    """Сохраняет среду как default Python, не затирая другие настройки VS Code."""
    paths = environment() if paths is None else paths
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


def setup(start: str | Path | None = None) -> PythonEnvironment:
    """Создаёт среду, ставит зависимости и настраивает VS Code."""
    paths = environment(start)
    existed = paths.python.is_file()
    action = (
        "Среда уже существует — пересоздание не требуется."
        if existed
        else "Создаю новую общую виртуальную среду."
    )
    _step(
        1,
        "PYTHON-СРЕДА",
        action,
        f"Среда:    {paths.environment_root.name}",
        f"Путь:     {paths.environment_root}",
        f"WinPython: {paths.base_python.parent}",
        "Режим:    прозрачное наследование пакетов WinPython",
    )
    create(paths)
    _step_done(1, "Python-среда готова")

    _step(
        2,
        "БИБЛИОТЕКИ",
        f"Устанавливаю и проверяю {len(PACKAGES) + 1} пакетов.",
        "Ниже будет подробный вывод pip; следующий этап отмечен таким же баннером.",
        "ВАЖНО: сообщения pip 'Attempting uninstall' можно игнорировать.",
        "Они ожидаемы для среды, наследующей WinPython; важен итог установки.",
    )
    install(paths)
    _step_done(2, "Библиотеки установлены и импорты проверены")

    _step(
        3,
        "ИНТЕРПРЕТАТОР ПРОЕКТА",
        "Меняю python.defaultInterpreterPath текущего проекта.",
        f"Интерпретатор: {paths.python}",
    )
    configure_vscode(paths)
    _step_done(3, f"VS Code настроен: {paths.settings_path}")

    print()
    print("═" * _BANNER_WIDTH)
    print("✓ ZEMI ARSENAL PYTHON ГОТОВ")
    print(f"Интерпретатор: {paths.python}")
    print("═" * _BANNER_WIDTH)
    return paths


__all__ = [
    "IMPORTS",
    "PACKAGES",
    "PythonEnvironment",
    "PythonEnvironmentConfig",
    "check",
    "config",
    "configure_vscode",
    "create",
    "environment",
    "install",
    "setup",
]
