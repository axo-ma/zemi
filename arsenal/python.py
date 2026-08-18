"""Create and verify Python venvs for ZEMI Components."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json, os, re, shutil, subprocess, sys, tomllib
from pathlib import Path
from uuid import uuid4

INSTANCE_MARKERS = (".zemiinst_dev", ".zemiinst_exp", ".zemiinst_prod")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "zemi_python_venv.toml"
PACKAGES = ("openai>=1.30.0", "httpx>=0.27.0", "pydantic>=2.7.0", "starlette==0.46.2", "cryptography==44.0.0", "joserfc==1.1.0", "python-calamine>=0.2.0", "openpyxl>=3.1.0", "markitdown>=0.0.1a0", "pandas>=2.2.0", "duckdb>=1.0.0", "fastembed>=0.3.0", "streamlit>=1.35.0", "dspy-ai>=2.4.0", "instructor>=1.3.0", "pydantic-ai>=0.0.14", "smolagents>=1.0.0", "litellm>=1.35.0", "outlines>=0.0.40", "guidance>=0.1.15", "llama-index-core>=0.10.0", "llama-index-llms-openai>=0.1.0", "llama-index-llms-openai-like", "unstructured-client>=0.25.0", "ipykernel")
IMPORTS = ("python_calamine", "openpyxl", "markitdown", "pandas", "duckdb", "fastembed", "streamlit", "dspy", "instructor", "pydantic_ai", "smolagents", "litellm", "outlines", "guidance", "llama_index.core", "llama_index.llms.openai_like", "unstructured_client", "ipykernel")
_SAFE = re.compile(r"[A-Za-z0-9_.-]+")

@dataclass(frozen=True)
class _ZConfig:
    REQUIRED_WINPYTHON_VERSION: str
    REQUIRED_Z_BUNDLE_VERSION: str

@dataclass(frozen=True)
class _CConfig:
    path: Path | None = None
    required_c_bundle_version: str | None = None
    c_bundle_packages: tuple[str, ...] = ()
    @property
    def active(self): return self.required_c_bundle_version is not None

@dataclass(frozen=True)
class _Paths:
    component_root: Path; instance_root: Path; base_python: Path
    environment_root: Path; python: Path; settings_path: Path

def _toml(path: Path) -> dict:
    try:
        with path.open("rb") as stream: return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Invalid TOML {path}: {error}") from error

def _name(values, key, path):
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: parameter {key} must be a non-empty string")
    if not _SAFE.fullmatch(value) or value in (".", ".."):
        raise ValueError(f"{path}: parameter {key} cannot be used in a venv name")
    return value

def _z_config(path=CONFIG_PATH):
    values = _toml(path)
    return _ZConfig(_name(values, "REQUIRED_WINPYTHON_VERSION", path), _name(values, "REQUIRED_Z_BUNDLE_VERSION", path))

def _c_config(path):
    values = _toml(path)
    if "REQUIRED_C_BUNDLE_VERSION" not in values: return _CConfig(path)
    version = _name(values, "REQUIRED_C_BUNDLE_VERSION", path)
    match = re.fullmatch(r"(.+)(\d{6})", version)
    if not match: raise ValueError(f"{path}: REQUIRED_C_BUNDLE_VERSION must end with a YYMMDD RunID")
    prefix, runid = match.groups()
    if not _SAFE.fullmatch(prefix): raise ValueError(f"{path}: invalid REQUIRED_C_BUNDLE_VERSION prefix")
    try: datetime.strptime(runid, "%y%m%d")
    except ValueError as error: raise ValueError(f"{path}: invalid YYMMDD RunID: {runid}") from error
    packages = values.get("C_BUNDLE_PACKAGES")
    if not isinstance(packages, list) or any(not isinstance(x, str) or not x.strip() for x in packages):
        raise ValueError(f"{path}: C_BUNDLE_PACKAGES must be a list of non-empty strings")
    if len(prefix) > 7: print(f"Warning: C-bundle prefix is longer than 7 characters: {prefix}")
    return _CConfig(path, version, tuple(packages))

def _root(start, markers):
    start = Path(start).resolve(); directory = start if start.is_dir() else start.parent
    for candidate in (directory, *directory.parents):
        found = [x for x in markers if (candidate / x).is_file()]
        if len(found) == 1: return candidate
        if len(found) > 1: raise RuntimeError(f"Multiple markers found in {candidate}")
    raise FileNotFoundError(f"No root found with a marker from: {markers}")

def _paths(component, z, c):
    instance = _root(component, INSTANCE_MARKERS)
    parts = [z.REQUIRED_Z_BUNDLE_VERSION, z.REQUIRED_WINPYTHON_VERSION]
    if c.active: parts.insert(0, c.required_c_bundle_version)
    root = instance / "_venvs" / "-".join(parts)
    base = instance / "_pythons" / z.REQUIRED_WINPYTHON_VERSION / "python" / "python.exe"
    return _Paths(component, instance, base, root, root / "Scripts/python.exe", component / ".vscode/settings.json")

def _cfg(path):
    if not path.is_file(): raise FileNotFoundError(f"pyvenv.cfg was not found: {path}")
    result = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line:
            key, value = line.split("=", 1); result[key.strip().lower()] = value.strip().strip("'\"")
    return result

def _atomic(source, target, instance):
    temp_root = instance / "_tmp"; temp_root.mkdir(parents=True, exist_ok=True)
    temp = temp_root / f"{target.name}.{uuid4().hex}.tmp"
    try: shutil.copy2(source, temp); os.replace(temp, target)
    finally:
        if temp.exists(): temp.unlink()

class PythonVenv:
    def __init__(self, paths, z, c):
        self._paths, self._z, self._c = paths, z, c
        self._component_specific = c.active; self._step_number = 0
        self._z_done = False; self._c_done = not c.active; self._script_failed = False

    @classmethod
    def from_config(cls, config_path="@comp/00_init.toml"):
        component = _root(Path.cwd(), (".zemicomp",))
        text = str(config_path).replace("\\", "/")
        if not text.startswith("@comp/"): raise ValueError("The configuration path must start with @comp/")
        c = _c_config(component / text.removeprefix("@comp/")); z = _z_config()
        return cls(_paths(component, z, c), z, c)

    @classmethod
    def standard(cls, start=None):
        component = _root(Path.cwd() if start is None else start, (".zemicomp",)); z = _z_config(); c = _CConfig()
        return cls(_paths(component, z, c), z, c)

    @classmethod
    def for_component(cls, *, component_name, version, start=None):
        combined = component_name + version
        if not _SAFE.fullmatch(combined): raise ValueError("component_name contains invalid characters")
        try: datetime.strptime(version, "%y%m%d")
        except ValueError as error: raise ValueError("version must use the YYMMDD format") from error
        component = _root(Path.cwd() if start is None else start, (".zemicomp",)); z = _z_config(); c = _CConfig(required_c_bundle_version=combined)
        return cls(_paths(component, z, c), z, c)

    @property
    def root(self): return self._paths.environment_root
    @property
    def python(self): return self._paths.python
    @property
    def prompt(self): return self._c.required_c_bundle_version or self._z.REQUIRED_Z_BUNDLE_VERSION

    def _begin(self, title): self._step_number += 1; print(f"ZEMI Python venv · [{self._step_number}] {title}"); return self._step_number
    def _done(self, number, text): print(f"✓ [{number}] {text}")

    def _verify_base(self):
        if not self.python.is_file():
            raise FileNotFoundError(
                "The required Python environment does not exist:\n"
                f"  {self.python}\n\n"
                "Run 00_init.py from the component root to create and configure this environment.\n"
                "After initialization completes, select the environment above as the notebook kernel or use it to run the Python script, then try again."
            )
        values = _cfg(self.root / "pyvenv.cfg")
        if values.get("include-system-site-packages", "").lower() != "true": raise RuntimeError("WinPython: include-system-site-packages is outdated")
        result = subprocess.run([str(self.python), "-c", "import sys; print(sys.base_prefix)"], cwd=self._paths.component_root, check=True, text=True, capture_output=True)
        if Path(result.stdout.strip()).resolve() != self._paths.base_python.parent.resolve(): raise RuntimeError("WinPython is outdated")

    def _verify_kernel(self):
        current_python = Path(sys.executable).resolve()
        required_python = self.python.resolve()
        if current_python != required_python:
            raise RuntimeError(
                "The current Python process is using the wrong environment.\n\n"
                "Current Python:\n"
                f"  {current_python}\n\n"
                "Required Python:\n"
                f"  {required_python}\n\n"
                "For a notebook, select the required Python environment as the kernel and run this cell again.\n"
                "For a Python script, run the script with the required Python interpreter.\n"
                "If the required environment is unavailable, run 00_init.py from the component root first."
            )

    def _verify_z_stamp(self):
        stamp = self.root / "zemi_python_venv.toml"
        try: installed = _z_config(stamp)
        except (ValueError, FileNotFoundError) as error: raise RuntimeError(f"Z-bundle stamp is missing or damaged at {stamp}: {error}") from error
        if installed != self._z: raise RuntimeError("Z-bundle is outdated. Run 00_init.py again")

    def _verify_c_stamp(self):
        if not self._c.active: return
        try: current = _c_config(self._c.path); installed = _c_config(self.root / "00_init.toml")
        except (ValueError, FileNotFoundError) as error: raise RuntimeError(f"C-bundle stamp is missing or damaged: {error}") from error
        if (current.required_c_bundle_version, current.c_bundle_packages) != (installed.required_c_bundle_version, installed.c_bundle_packages): raise RuntimeError("C-bundle is outdated. Run 00_init.py again")

    def create_if_missing(self):
        number = self._begin("CREATE VENV")
        if not self._paths.base_python.is_file(): raise FileNotFoundError(f"Base WinPython was not found: {self._paths.base_python}")
        created = not self.python.is_file()
        if created:
            self.root.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([str(self._paths.base_python), "-m", "venv", "--system-site-packages", "--prompt", self.prompt, str(self.root)], cwd=self._paths.component_root, check=True)
        self._verify_base(); self._done(number, "Python venv created" if created else "Python venv already exists")

    def install_zemi_packages(self):
        number = self._begin("ZEMI PACKAGES")
        if not self.python.is_file(): raise FileNotFoundError("Call create_if_missing() first")
        subprocess.run([str(self.python), "-m", "pip", "install", "--only-binary", ":all:", *PACKAGES], cwd=self._paths.component_root, check=True)
        script = "import importlib, os; os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'; " + f"[importlib.import_module(name) for name in {IMPORTS!r}]"
        subprocess.run([str(self.python), "-c", script], cwd=self._paths.component_root, check=True)
        _atomic(CONFIG_PATH, self.root / "zemi_python_venv.toml", self._paths.instance_root); self._z_done = True; self._done(number, "ZEMI packages installed and verified")

    def install_component_packages(self):
        number = self._begin("COMPONENT PACKAGES")
        if not self._c.active: self._done(number, "C-bundle disabled; step skipped"); return
        subprocess.run([str(self.python), "-m", "pip", "install", *self._c.c_bundle_packages], cwd=self._paths.component_root, check=True)
        self._c_done = True; self._done(number, "C-bundle packages installed")

    def install_packages(self, *packages):
        if not packages: return
        if not self._component_specific: raise RuntimeError("Component packages cannot be installed in the standard shared venv")
        subprocess.run([str(self.python), "-m", "pip", "install", *packages], cwd=self._paths.component_root, check=True)

    def run_script(self, script, *arguments):
        if not self._component_specific: raise RuntimeError("Component installation code cannot run in the standard shared venv")
        text = str(script).replace("\\", "/"); roots = {"@comp/": self._paths.component_root, "@inst/": self._paths.instance_root}
        path = next((root / text.removeprefix(prefix) for prefix, root in roots.items() if text.startswith(prefix)), None)
        if path is None: raise ValueError("The script path must start with @comp/ or @inst/")
        if not path.is_file(): raise FileNotFoundError(f"Installation script was not found: {path}")
        number = self._begin("COMPONENT INSTALLATION CODE")
        try: subprocess.run([str(self.python), str(path), *arguments], cwd=self._paths.component_root, check=True)
        except subprocess.CalledProcessError:
            self._script_failed = True
            raise
        self._done(number, "Installation script completed")

    def finalize_install(self):
        number = self._begin("FINALIZE INSTALLATION")
        if not self._z_done or not self._c_done or self._script_failed: raise RuntimeError("An incomplete installation cannot be finalized")
        if self._c.active: _atomic(self._c.path, self.root / "00_init.toml", self._paths.instance_root)
        self._done(number, "Installation finalized")

    def verify(self):
        number = self._begin("VERIFY VENV"); self._verify_base(); self._verify_kernel(); self._verify_z_stamp(); self._verify_c_stamp(); self._done(number, "Python venv and stamps are up to date")

    def set_as_vscode_interpreter(self):
        number = self._begin("VS CODE INTERPRETER"); settings = {}
        if self._paths.settings_path.is_file():
            settings = json.loads(self._paths.settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict): raise ValueError("VS Code settings must be an object")
        settings.pop("python-envs.pythonProjects", None); settings.pop("python-envs.workspaceSearchPaths", None)
        relative = os.path.relpath(self.python, self._paths.component_root).replace("\\", "/")
        settings.update({"python.defaultInterpreterPath": f"${{workspaceFolder}}/{relative}", "python.terminal.activateEnvironment": True})
        self._paths.settings_path.parent.mkdir(parents=True, exist_ok=True); self._paths.settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"); self._done(number, "VS Code configured")

__all__ = ["IMPORTS", "PACKAGES", "PythonVenv"]
