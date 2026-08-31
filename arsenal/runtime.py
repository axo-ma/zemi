"""Session for managing ZEMI Arsenal processes and local resources."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .. import env, toml
from .downloads import DownloadError, download_llama, download_model
from .objects import Llama, Model, NamedObjects


__all__ = ["ArsenalSession"]


_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent
    / "llm_curated_set_router_mode.toml"
)
_DEFAULT_CONFIG_LABEL = "zemi/llm_curated_set_router_mode.toml"
_ARSENAL_MODES = {"model", "router"}


class ArsenalSession:
    """Arsenal object tree with lazy model activation.

    With no configuration argument, use the curated Router Mode model set.
    """

    def __init__(
        self,
        config_path: str | Path | dict[str, Any] | None = None,
    ) -> None:
        if config_path is None:
            self.config_path = _DEFAULT_CONFIG_LABEL
            self.config = toml.load(_DEFAULT_CONFIG_PATH)
        elif isinstance(config_path, dict):
            self.config_path: str | None = None
            self.config = config_path
        else:
            self.config_path = str(config_path).replace("\\", "/")
            self.config = toml.load(self._resolve_zemi_path(config_path))

        try:
            arsenal_config = self.config["arsenal"]
        except (KeyError, TypeError) as error:
            raise ValueError("TOML must contain the [arsenal] table") from error
        if not isinstance(arsenal_config, dict):
            raise ValueError("arsenal must be a table")

        mode = arsenal_config.get("mode")
        if not isinstance(mode, str) or mode not in _ARSENAL_MODES:
            raise ValueError(
                "arsenal.mode is required and must be exactly 'model' or 'router'"
            )
        self.mode = mode

        try:
            configs = arsenal_config["llamas"]
        except KeyError as error:
            raise ValueError(
                "TOML must contain the [[arsenal.llamas]] array of tables"
            ) from error
        if not isinstance(configs, list):
            raise ValueError("arsenal.llamas must be an array of tables")

        self._active = False
        self._router_mode = self.mode == "router"
        self._llama_paths: dict[str, Path] = {}
        self._model_paths: dict[str, Path] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._running_models: dict[str, set[str]] = {}
        self.llamas = NamedObjects([
            Llama(config, self._activate_model) for config in configs
        ])

    def model(self, name: str) -> Model:
        """Return one model by name without activating non-matching models."""
        matches: list[tuple[Llama, Model]] = []
        for llama in self.llamas._iter_raw():
            for model in llama.models._iter_raw():
                if model.name == name:
                    matches.append((llama, model))
        if not matches:
            raise LookupError(f"No Arsenal model named {name!r} was found")
        if len(matches) > 1:
            servers = ", ".join(llama.name for llama, _model in matches)
            raise LookupError(
                f"Arsenal model name {name!r} is ambiguous across servers: {servers}"
            )
        llama, _model = matches[0]
        return llama.models[name]

    def download(self) -> None:
        """Download all llama.cpp builds and models from TOML in advance."""
        llama_items = list(self.llamas._iter_raw())
        model_count = sum(
            len(llama.models)
            for llama in llama_items
        )

        print()
        print("═" * 78)
        print("ZEMI Arsenal · PRE-DOWNLOAD")
        if self.config_path is not None:
            print(f"TOML: {self.config_path}")
        print(
            f"Llama servers: {len(llama_items)} · "
            f"models: {model_count}"
        )
        print("═" * 78)

        model_number = 0
        try:
            for llama_number, llama in enumerate(llama_items, start=1):
                print()
                print("─" * 78)
                print(
                    f"LLAMA [{llama_number}/{len(llama_items)}] · "
                    f"{llama.name} · {llama.llama_build}"
                )
                print("─" * 78)
                if llama.name not in self._llama_paths:
                    self._llama_paths[llama.name] = download_llama(
                        llama.llama_build
                    )
                else:
                    print(f"llama.cpp {llama.llama_build} is already prepared")

                for model in llama.models._iter_raw():
                    model_number += 1
                    key = self._model_key(llama, model)
                    print()
                    print(
                        f"MODEL [{model_number}/{model_count}] · {key}\n"
                        f"{model.owner}/{model.repository}/{model.filename}"
                    )
                    if key not in self._model_paths:
                        self._model_paths[key] = download_model(
                            model.owner,
                            model.repository,
                            model.filename,
                            source=model.source,
                        )
                    else:
                        print(f"Model {key} is already prepared")
        except Exception as error:
            print("!" * 78)
            print("✗ Arsenal pre-download stopped")
            print(f"{type(error).__name__}: {error}")
            print("!" * 78)
            raise

        print()
        print("═" * 78)
        print(
            f"✓ All Arsenal resources downloaded · "
            f"servers: {len(self._llama_paths)} · "
            f"models: {len(self._model_paths)}"
        )
        print("No processes started; models activate after arsenal.begin().")
        print("═" * 78)

    @staticmethod
    def _resolve_zemi_path(value: str | Path) -> Path:
        path = str(value).replace("\\", "/")
        for prefix, root in (("@comp/", env.path.comp.root), ("@inst/", env.path.inst)):
            if not path.startswith(prefix):
                continue
            relative = Path(path.removeprefix(prefix))
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Invalid ZEMI path: {path!r}")
            return root / relative
        raise ValueError("Path must start with @comp/ or @inst/")

    def _begin(
        self,
        stop_arsenal_before_begin: bool,
    ) -> None:
        """Enable lazy model activation without downloading or starting models."""
        self._active = False
        if stop_arsenal_before_begin:
            self._stop_arsenal()

        if self.mode == "model":
            invalid = [
                f"{llama.name} ({len(llama.models)} models)"
                for llama in self.llamas._iter_raw()
                if len(llama.models) != 1
            ]
            if invalid:
                details = ", ".join(invalid)
                raise ValueError(
                    "In Model Mode, every llama server must contain exactly "
                    f"one model. Violation: {details}"
                )

        self._active = True

        mode = "ROUTER MODE" if self._router_mode else "MODEL MODE"
        self._print_operation_header(
            f"ARSENAL READY · {mode}",
            len(self.llamas),
        )
        print("Download and startup are deferred until the model is first accessed.")
        first_llama = next(self.llamas._iter_raw())
        first_model = next(first_llama.models._iter_raw())
        print(
            f'Example: arsenal.llamas["{first_llama.name}"]'
            f'.models["{first_model.name}"]'
        )
        print("═" * 78)

    def _end(self, stop_arsenal_after_end: bool) -> None:
        """Disable lazy activation and stop servers when requested."""
        self._active = False
        if stop_arsenal_after_end:
            self._stop_arsenal()

    def _activate_model(self, llama: Llama, model: Model) -> None:
        """Prepare a model and start its llama server on first access."""
        if not self._active:
            return

        running_models = self._running_models.get(llama.name, set())
        process = self._processes.get(llama.name)
        if model.name in running_models:
            if process is not None and process.poll() is None:
                return
            if self._is_server_ready(llama.host, llama.port):
                return

        self._print_activation_header(llama, model)
        try:
            self._prepare_llama(llama)
            self._prepare_model(llama, model)

            if self._router_mode:
                models_to_run = set(running_models) | {model.name}
                if process is not None and process.poll() is None:
                    print(f"[3/4] Router {llama.name} is already running")
                else:
                    print(f"[3/4] Starting {llama.name} in Router Mode...")
                    preset_path = self._write_router_preset(llama)
                    command = [
                        str(self._server_path(llama)),
                        "--models-preset", str(preset_path),
                        "--host", llama.host,
                        "--port", str(llama.port),
                    ]
                    self._start_server(llama, command)
                print(f"[4/4] Loading model {model.alias} in Router Mode...")
                self._load_router_model(llama, model)
            else:
                models_to_run = {model.name}
                print(f"[3/3] Starting {llama.name} with model {model.name}...")
                command = [
                    str(self._server_path(llama)),
                    "--model", str(self._model_path(llama, model)),
                    "--alias", model.alias,
                    "--host", llama.host,
                    "--port", str(llama.port),
                    "--ctx-size", str(model.ctx_size),
                    "--threads", str(model.threads),
                    "--threads-batch", str(model.threads_batch),
                    "--reasoning", model.reasoning,
                ]
                self._start_server(llama, command)
            self._running_models[llama.name] = models_to_run
        except Exception as error:
            print("!" * 78)
            print(f"✗ Failed to activate {llama.name}/{model.name}")
            print(f"{type(error).__name__}: {error}")
            print("!" * 78)
            raise

        print("═" * 78)
        print(f"✓ Model ready: {llama.name}/{model.name}")
        print(f"  Server: http://{llama.host}:{llama.port}")
        print("═" * 78)

    def _prepare_llama(self, llama: Llama) -> None:
        total = 4 if self._router_mode else 3
        if llama.name in self._llama_paths:
            print(f"[1/{total}] llama.cpp {llama.llama_build} is already prepared")
            return

        print(f"[1/{total}] Checking llama.cpp {llama.llama_build}...")
        try:
            self._llama_paths[llama.name] = download_llama(llama.llama_build)
        except DownloadError as error:
            raise DownloadError(
                f"Failed to prepare llama-server {llama.name!r} "
                f"({llama.llama_build}).\n\n{error}"
            ) from error

    def _prepare_model(self, llama: Llama, model: Model) -> None:
        total = 4 if self._router_mode else 3
        key = self._model_key(llama, model)
        if key in self._model_paths:
            print(f"[2/{total}] Model {key} is already prepared")
            return

        print(f"[2/{total}] Checking model {key}...")
        try:
            self._model_paths[key] = download_model(
                model.owner,
                model.repository,
                model.filename,
                source=model.source,
            )
        except DownloadError as error:
            raise DownloadError(
                f"Failed to prepare model {key!r}.\n"
                f"Model: {model.owner}/{model.repository}/{model.filename}"
                f"\n\n{error}"
            ) from error

    def _stop_arsenal(self) -> None:
        """Stop running Arsenal resources."""
        llama_items = list(self.llamas._iter_raw())
        self._print_operation_header("STOP ARSENAL", len(llama_items))

        for number, llama in enumerate(llama_items, start=1):
            print(
                f"[{number}/{len(llama_items)}] {llama.name} · "
                f"{llama.host}:{llama.port}"
            )
            status = self._stop_llama(llama)
            print(f"    {status}")

        self._print_operation_result("✓ Arsenal stopped")

    def _stop_llama(self, llama: Llama) -> str:
        process = self._processes.pop(llama.name, None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            status = f"✓ stopped · PID {process.pid}"
        elif self._stop_server_on_port(llama.port):
            status = "✓ found and stopped"
        else:
            status = "· not running"
        self._running_models.pop(llama.name, None)
        return status

    @staticmethod
    def _print_operation_header(title: str, server_count: int) -> None:
        print()
        print("═" * 78)
        print(f"ZEMI Arsenal · {title}")
        print(f"Llama servers in configuration: {server_count}")
        print("═" * 78)

    @staticmethod
    def _print_operation_result(message: str) -> None:
        print("═" * 78)
        print(message)
        print("═" * 78)

    def _print_activation_header(self, llama: Llama, model: Model) -> None:
        print()
        print("═" * 78)
        print("ZEMI Arsenal · LAZY MODEL ACTIVATION")
        print(f"Model: {llama.name}/{model.name} · {model.alias}")
        print(f"Llama:  {llama.llama_build} · {llama.host}:{llama.port}")
        if self.config_path is not None:
            print(f"TOML:   {self.config_path}")
        print("═" * 78)

    @staticmethod
    def _model_key(llama: Llama, model: Model) -> str:
        return f"{llama.name}/{model.name}"

    def _server_path(self, llama: Llama) -> Path:
        directory = self._llama_paths.get(llama.name)
        if directory is None:
            raise RuntimeError(f"llama.cpp for {llama.name!r} is not prepared yet")
        path = directory / "llama-server.exe"
        if not path.is_file():
            raise FileNotFoundError(f"llama-server.exe was not found: {path.resolve()}")
        return path

    def _model_path(self, llama: Llama, model: Model) -> Path:
        key = self._model_key(llama, model)
        path = self._model_paths.get(key)
        if path is None:
            raise RuntimeError(f"Model {key!r} is not prepared yet")
        if not path.is_file():
            raise FileNotFoundError(f"Model file was not found: {path.resolve()}")
        return path

    def _start_server(self, llama: Llama, command: list[str]) -> None:
        if self._is_server_ready(llama.host, llama.port):
            raise RuntimeError(
                f"An HTTP server is already running at {llama.host}:{llama.port}. "
                "Use stop_arsenal_before_begin=True to stop Arsenal."
            )

        process = subprocess.Popen(command)
        self._processes[llama.name] = process
        deadline = time.monotonic() + float(llama.startup_timeout)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self._processes.pop(llama.name, None)
                raise RuntimeError(
                    f"llama-server {llama.name!r} exited with code "
                    f"{process.returncode}"
                )
            if self._is_server_ready(llama.host, llama.port):
                print(f"    ✓ server ready · PID {process.pid}")
                return
            time.sleep(0.5)

        process.terminate()
        self._processes.pop(llama.name, None)
        raise TimeoutError(
            f"llama-server {llama.name!r} did not start within "
            f"{float(llama.startup_timeout):.1f} seconds"
        )

    def _write_router_preset(
        self,
        llama: Llama,
    ) -> Path:
        preset_path = env.path.tmp / f"zemi-arsenal-{llama.name}.ini"
        lines = ["version = 1", ""]
        models = list(llama.models._iter_raw())
        for model in models:
            configured_path = (
                env.path.model(
                    model.owner,
                    model.repository,
                    model.filename,
                    source=model.source,
                )
                / model.filename
            )
            lines.extend([
                f"[{model.alias}]",
                f"model = {configured_path}",
                f"ctx-size = {model.ctx_size}",
                f"threads = {model.threads}",
                f"threads-batch = {model.threads_batch}",
                f"reasoning = {model.reasoning}",
                "",
            ])
        preset_path.parent.mkdir(parents=True, exist_ok=True)
        preset_path.write_text("\n".join(lines), encoding="utf-8")
        print(
            f"    Preset {llama.name}: {len(models)} models · "
            f"{preset_path}"
        )
        return preset_path

    @staticmethod
    def _load_router_model(llama: Llama, model: Model) -> None:
        request = Request(
            f"http://{llama.host}:{llama.port}/models/load",
            data=json.dumps({"model": model.alias}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=float(llama.startup_timeout)) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Router {llama.name!r} could not load model "
                f"{model.alias!r}: {error}"
            ) from error
        if result.get("success") is not True:
            raise RuntimeError(
                f"Router {llama.name!r} rejected loading model "
                f"{model.alias!r}: {result}"
            )
        print(f"    ✓ model {model.alias} loaded")

    @staticmethod
    def _is_server_ready(host: str, port: int, timeout: float = 0.5) -> bool:
        try:
            with urlopen(f"http://{host}:{port}/health", timeout=timeout):
                return True
        except (URLError, TimeoutError):
            return False

    @staticmethod
    def _stop_server_on_port(port: int) -> bool:
        command = f"""
        $connection = Get-NetTCPConnection -LocalPort {port} -State Listen `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $connection) {{ Write-Output 'NOT_FOUND'; exit }}
        $process = Get-Process -Id $connection.OwningProcess `
            -ErrorAction SilentlyContinue
        if (-not $process) {{ Write-Output 'NOT_FOUND'; exit }}
        if ($process.ProcessName -ne 'llama-server') {{
            Write-Output "WRONG_PROCESS:$($process.ProcessName):$($process.Id)"
            exit
        }}
        Stop-Process -Id $process.Id -Force
        Write-Output "STOPPED:$($process.Id)"
        """
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout.strip()
        if output.startswith("STOPPED:"):
            return True
        if output == "NOT_FOUND":
            return False
        if output.startswith("WRONG_PROCESS:"):
            _, process_name, pid = output.split(":", 2)
            raise RuntimeError(
                f"Port {port} is occupied by another process: {process_name}, PID {pid}"
            )
        error = result.stderr.strip() or output or "unknown error"
        raise RuntimeError(f"Could not stop the server on port {port}: {error}")
