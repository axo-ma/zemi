"""Ленивое управление процессами и локальными ресурсами ZEMI Arsenal."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .. import env, toml
from .arsenal_objects import Llama, Model, NamedObjects
from .llamas import DownloadError, download_llama, download_model


__all__ = ["Arsenal"]


class Arsenal:
    """Объектное дерево playbook с ленивой активацией моделей."""

    def __init__(self, config_path: str | Path | dict[str, Any]) -> None:
        if isinstance(config_path, dict):
            self.config_path: str | None = None
            self.config = config_path
        else:
            self.config_path = str(config_path).replace("\\", "/")
            self.config = toml.load(self._resolve_zemi_path(config_path))

        try:
            configs = self.config["arsenal"]["llamas"]
        except (KeyError, TypeError) as error:
            raise ValueError(
                "TOML должен содержать массив таблиц [[arsenal.llamas]]"
            ) from error
        if not isinstance(configs, list):
            raise ValueError("arsenal.llamas должен быть массивом таблиц")

        self._playbook_active = False
        self._llama_router_mode = False
        self._llama_paths: dict[str, Path] = {}
        self._model_paths: dict[str, Path] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._running_models: dict[str, set[str]] = {}
        self.llamas = NamedObjects([
            Llama(config, self._activate_model) for config in configs
        ])

    def download(self) -> None:
        """Заранее скачивает все llama.cpp-сборки и модели из TOML."""
        llama_items = list(self.llamas._iter_raw())
        model_count = sum(
            len(llama.models)
            for llama in llama_items
        )

        print()
        print("═" * 78)
        print("ZEMI Playbook · ПРЕДВАРИТЕЛЬНОЕ СКАЧИВАНИЕ ARSENAL")
        if self.config_path is not None:
            print(f"TOML: {self.config_path}")
        print(
            f"Llama-серверов: {len(llama_items)} · "
            f"моделей: {model_count}"
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
                    print(f"llama.cpp {llama.llama_build} уже подготовлен")

                for model in llama.models._iter_raw():
                    model_number += 1
                    key = self._model_key(llama, model)
                    print()
                    print(
                        f"МОДЕЛЬ [{model_number}/{model_count}] · {key}\n"
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
                        print(f"Модель {key} уже подготовлена")
        except Exception as error:
            print("!" * 78)
            print("✗ Предварительное скачивание Arsenal остановлено")
            print(f"{type(error).__name__}: {error}")
            print("!" * 78)
            raise

        print()
        print("═" * 78)
        print(
            f"✓ Все ресурсы Arsenal скачаны · "
            f"серверов: {len(self._llama_paths)} · "
            f"моделей: {len(self._model_paths)}"
        )
        print("Процессы не запущены; модели активируются через begin_playbook().")
        print("═" * 78)

    @staticmethod
    def _resolve_zemi_path(value: str | Path) -> Path:
        path = str(value).replace("\\", "/")
        for prefix, root in (("@comp/", env.path.comp), ("@inst/", env.path.inst)):
            if not path.startswith(prefix):
                continue
            relative = Path(path.removeprefix(prefix))
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Некорректный ZEMI-путь: {path!r}")
            return root / relative
        raise ValueError("Путь должен начинаться с @comp/ или @inst/")

    def begin_playbook(
        self,
        stop_arsenal_before_begin: bool,
        llama_router_mode: bool = False,
    ) -> None:
        """Включает ленивую активацию моделей, не скачивая и не запуская их."""
        self._playbook_active = False
        if stop_arsenal_before_begin:
            self._stop_arsenal()

        if not llama_router_mode:
            invalid = [
                f"{llama.name} ({len(llama.models)} моделей)"
                for llama in self.llamas._iter_raw()
                if len(llama.models) != 1
            ]
            if invalid:
                details = ", ".join(invalid)
                raise ValueError(
                    "Без Router Mode каждый llama-сервер должен содержать ровно "
                    f"одну модель. Нарушение: {details}"
                )

        self._llama_router_mode = llama_router_mode
        self._playbook_active = True

        mode = "ROUTER MODE" if llama_router_mode else "MODEL MODE"
        self._print_operation_header(
            f"ARSENAL ГОТОВ · {mode}",
            len(self.llamas),
        )
        print("Скачивание и запуск отложены до первого обращения к модели.")
        print("Пример: arsenal.llamas[\"primary\"].models[\"qwen\"]")
        print("═" * 78)

    def end_playbook(self, stop_arsenal_after_end: bool) -> None:
        """Отключает ленивую активацию и при необходимости останавливает серверы."""
        self._playbook_active = False
        if stop_arsenal_after_end:
            self._stop_arsenal()

    def _activate_model(self, llama: Llama, model: Model) -> None:
        """При первом обращении готовит модель и запускает её llama-сервер."""
        if not self._playbook_active:
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

            if self._llama_router_mode:
                models_to_run = set(running_models) | {model.name}
                if process is not None and process.poll() is None:
                    print(f"[3/4] Router {llama.name} уже запущен")
                else:
                    print(f"[3/4] Запускаю {llama.name} в Router Mode...")
                    preset_path = self._write_router_preset(llama)
                    command = [
                        str(self._server_path(llama)),
                        "--models-preset", str(preset_path),
                        "--host", llama.host,
                        "--port", str(llama.port),
                    ]
                    self._start_server(llama, command)
                print(f"[4/4] Загружаю модель {model.alias} в Router Mode...")
                self._load_router_model(llama, model)
            else:
                models_to_run = {model.name}
                print(f"[3/3] Запускаю {llama.name} с моделью {model.name}...")
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
            print(f"✗ Не удалось активировать {llama.name}/{model.name}")
            print(f"{type(error).__name__}: {error}")
            print("!" * 78)
            raise

        print("═" * 78)
        print(f"✓ Модель готова: {llama.name}/{model.name}")
        print(f"  Сервер: http://{llama.host}:{llama.port}")
        print("═" * 78)

    def _prepare_llama(self, llama: Llama) -> None:
        total = 4 if self._llama_router_mode else 3
        if llama.name in self._llama_paths:
            print(f"[1/{total}] llama.cpp {llama.llama_build} уже подготовлен")
            return

        print(f"[1/{total}] Проверяю llama.cpp {llama.llama_build}...")
        try:
            self._llama_paths[llama.name] = download_llama(llama.llama_build)
        except DownloadError as error:
            raise DownloadError(
                f"Не удалось подготовить llama-server {llama.name!r} "
                f"({llama.llama_build}).\n\n{error}"
            ) from error

    def _prepare_model(self, llama: Llama, model: Model) -> None:
        total = 4 if self._llama_router_mode else 3
        key = self._model_key(llama, model)
        if key in self._model_paths:
            print(f"[2/{total}] Модель {key} уже подготовлена")
            return

        print(f"[2/{total}] Проверяю модель {key}...")
        try:
            self._model_paths[key] = download_model(
                model.owner,
                model.repository,
                model.filename,
                source=model.source,
            )
        except DownloadError as error:
            raise DownloadError(
                f"Не удалось подготовить модель {key!r}.\n"
                f"Модель: {model.owner}/{model.repository}/{model.filename}"
                f"\n\n{error}"
            ) from error

    def _stop_arsenal(self) -> None:
        """Останавливает работающие ресурсы Arsenal."""
        llama_items = list(self.llamas._iter_raw())
        self._print_operation_header("ОСТАНОВКА ARSENAL", len(llama_items))

        for number, llama in enumerate(llama_items, start=1):
            print(
                f"[{number}/{len(llama_items)}] {llama.name} · "
                f"{llama.host}:{llama.port}"
            )
            status = self._stop_llama(llama)
            print(f"    {status}")

        self._print_operation_result("✓ Arsenal остановлен")

    def _stop_llama(self, llama: Llama) -> str:
        process = self._processes.pop(llama.name, None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            status = f"✓ остановлен · PID {process.pid}"
        elif self._stop_server_on_port(llama.port):
            status = "✓ найден и остановлен"
        else:
            status = "· не запущен"
        self._running_models.pop(llama.name, None)
        return status

    @staticmethod
    def _print_operation_header(title: str, server_count: int) -> None:
        print()
        print("═" * 78)
        print(f"ZEMI Playbook · {title}")
        print(f"Llama-серверов в конфигурации: {server_count}")
        print("═" * 78)

    @staticmethod
    def _print_operation_result(message: str) -> None:
        print("═" * 78)
        print(message)
        print("═" * 78)

    def _print_activation_header(self, llama: Llama, model: Model) -> None:
        print()
        print("═" * 78)
        print("ZEMI Playbook · ЛЕНИВАЯ АКТИВАЦИЯ МОДЕЛИ")
        print(f"Модель: {llama.name}/{model.name} · {model.alias}")
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
            raise RuntimeError(f"llama.cpp для {llama.name!r} ещё не подготовлен")
        path = directory / "llama-server.exe"
        if not path.is_file():
            raise FileNotFoundError(f"llama-server.exe не найден: {path.resolve()}")
        return path

    def _model_path(self, llama: Llama, model: Model) -> Path:
        key = self._model_key(llama, model)
        path = self._model_paths.get(key)
        if path is None:
            raise RuntimeError(f"Модель {key!r} ещё не подготовлена")
        if not path.is_file():
            raise FileNotFoundError(f"Файл модели не найден: {path.resolve()}")
        return path

    def _start_server(self, llama: Llama, command: list[str]) -> None:
        if self._is_server_ready(llama.host, llama.port):
            raise RuntimeError(
                f"На {llama.host}:{llama.port} уже работает HTTP-сервер. "
                "Используйте stop_arsenal_before_begin=True для остановки Arsenal."
            )

        process = subprocess.Popen(command)
        self._processes[llama.name] = process
        deadline = time.monotonic() + float(llama.startup_timeout)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self._processes.pop(llama.name, None)
                raise RuntimeError(
                    f"llama-server {llama.name!r} завершился с кодом "
                    f"{process.returncode}"
                )
            if self._is_server_ready(llama.host, llama.port):
                print(f"    ✓ сервер готов · PID {process.pid}")
                return
            time.sleep(0.5)

        process.terminate()
        self._processes.pop(llama.name, None)
        raise TimeoutError(
            f"llama-server {llama.name!r} не запустился за "
            f"{float(llama.startup_timeout):.1f} секунд"
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
            f"    Пресет {llama.name}: {len(models)} моделей · "
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
                f"Router {llama.name!r} не смог загрузить модель "
                f"{model.alias!r}: {error}"
            ) from error
        if result.get("success") is not True:
            raise RuntimeError(
                f"Router {llama.name!r} отклонил загрузку модели "
                f"{model.alias!r}: {result}"
            )
        print(f"    ✓ модель {model.alias} загружена")

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
                f"Порт {port} занят другим процессом: {process_name}, PID {pid}"
            )
        error = result.stderr.strip() or output or "неизвестная ошибка"
        raise RuntimeError(f"Не удалось остановить сервер на порту {port}: {error}")
