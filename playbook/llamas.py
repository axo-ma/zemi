"""Скачивание llama.cpp и GGUF-моделей для ZEMI Playbook."""

from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from .. import env


__all__ = [
    "DownloadError",
    "download_llama",
    "download_model",
]


class DownloadError(RuntimeError):
    """Ожидаемая ошибка скачивания внешнего ресурса."""


def _display_zemi_path(path: Path) -> str:
    """Представляет файловый путь через маркер ZEMI."""
    path = path.resolve()
    for marker, root in (("@comp", env.path.comp), ("@inst", env.path.inst)):
        try:
            relative = path.relative_to(root.resolve())
        except ValueError:
            continue
        return f"{marker}/{relative.as_posix()}"
    raise ValueError(f"Путь находится вне ZEMI Instance: {path.name}")


def _format_size(size: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            return f"{size:.1f} {unit}" if unit != "Б" else f"{size:.0f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


class _Progress:
    """Одинаково обновляет одну строку в терминале и одну область в Jupyter."""

    def __init__(self, label: str, total: int | None) -> None:
        self.label = label
        self.total = total
        self.started_at = time.monotonic()
        self.last_update = 0.0
        self.display_handle = None

        try:
            from IPython import get_ipython
            from IPython.display import display

            shell = get_ipython()
            if shell is not None and shell.__class__.__name__ == "ZMQInteractiveShell":
                self.display_handle = display("", display_id=True)
        except ImportError:
            pass

    def update(self, loaded: int, *, done: bool = False) -> None:
        now = time.monotonic()
        if not done and now - self.last_update < 0.1:
            return
        self.last_update = now

        elapsed = max(now - self.started_at, 0.001)
        speed = f"{_format_size(loaded / elapsed)}/с"
        if self.total:
            percent = min(loaded / self.total * 100, 100.0)
            amount = f"{_format_size(loaded)} / {_format_size(self.total)}"
            message = f"{self.label}: {percent:5.1f}% · {amount} · {speed}"
        else:
            message = f"{self.label}: {_format_size(loaded)} · {speed}"

        if done:
            message += " · готово"

        if self.display_handle is not None:
            self.display_handle.update(message)
        else:
            print(f"\r{message}", end="\n" if done else "", flush=True)


def _download(
    url: str,
    destination: Path,
    *,
    label: str,
    size_file: Path | None = None,
) -> None:
    """Потоково скачивает URL с прогрессом и атомарно переносит файл на место."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
    request = Request(url, headers={"User-Agent": "ZEMI"})

    try:
        with urlopen(request) as response, temporary.open("wb") as output:
            header = response.headers.get("Content-Length")
            total = int(header) if header and header.isdigit() else None
            if size_file is not None:
                if total is None:
                    raise DownloadError(
                        "Сервер не сообщил размер модели.\n"
                        f"Адрес: {url}\n"
                        "Без размера безопасное скачивание модели невозможно."
                    )
                size_file.write_text(str(total), encoding="ascii")
            progress = _Progress(label, total)
            loaded = 0

            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                loaded += len(chunk)
                progress.update(loaded)

            if total is not None and loaded != total:
                raise DownloadError(
                    "Скачивание завершилось раньше времени.\n"
                    f"Получено: {_format_size(loaded)}\n"
                    f"Ожидалось: {_format_size(total)}\n"
                    f"Адрес: {url}\n"
                    "Временный файл будет удалён; повторите скачивание."
                )

            progress.update(loaded, done=True)
        temporary.replace(destination)
    except HTTPError as error:
        temporary.unlink(missing_ok=True)
        reason = error.reason or "без пояснения"
        raise DownloadError(
            "Сервер не отдал запрошенный файл.\n"
            f"HTTP-статус: {error.code} {reason}\n"
            f"Адрес: {url}\n"
            "Проверьте имя репозитория, имя файла и доступность релиза."
        ) from None
    except URLError as error:
        temporary.unlink(missing_ok=True)
        raise DownloadError(
            "Не удалось подключиться к серверу скачивания.\n"
            f"Причина: {error.reason}\n"
            f"Адрес: {url}\n"
            "Проверьте подключение к интернету и доступность сайта."
        ) from None
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise DownloadError(
            "Не удалось сохранить скачиваемый файл.\n"
            f"Назначение: {_display_zemi_path(destination)}\n"
            f"Причина: {error}"
        ) from None
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _model_size_file(model_path: Path) -> Path:
    """Возвращает путь к файлу с ожидаемым размером модели."""
    return model_path.with_name(f"{model_path.name}.size")


def _read_model_size(size_file: Path) -> int | None:
    """Читает сохранённый размер модели или возвращает ``None``."""
    try:
        value = size_file.read_text(encoding="ascii").strip()
    except (FileNotFoundError, OSError, UnicodeError):
        return None
    return int(value) if value.isdigit() and int(value) > 0 else None


def _is_complete_model(model_path: Path, size_file: Path) -> bool:
    """Проверяет модель по локальному файлу ожидаемого размера."""
    if not model_path.is_file():
        return False

    expected_size = _read_model_size(size_file)
    actual_size = model_path.stat().st_size

    if expected_size is None:
        size_file.write_text(str(actual_size), encoding="ascii")
        print(
            "Создан локальный файл размера для ранее скачанной модели: "
            f"{_display_zemi_path(size_file)}"
        )
        return True

    if actual_size == expected_size:
        return True

    print(
        "Обнаружен неполный файл модели:\n"
        f"  файл: {_display_zemi_path(model_path)}\n"
        f"  скачано: {_format_size(actual_size)}\n"
        f"  ожидается: {_format_size(expected_size)}\n"
        "Модель будет скачана заново."
    )
    return False


def download_llama(build: str, *, url: str | None = None) -> Path:
    """Скачивает и распаковывает Windows CPU-сборку llama.cpp в ZEMI Instance."""
    normalized_build = build.removeprefix("llama:")
    target = env.path.llama(normalized_build)
    server = target / "llama-server.exe"

    if server.is_file():
        print(
            f"llama.cpp {normalized_build} уже скачан: "
            f"{_display_zemi_path(target)}"
        )
        return target

    archive_url = url or (
        "https://github.com/ggml-org/llama.cpp/releases/download/"
        f"{normalized_build}/llama-{normalized_build}-bin-win-cpu-x64.zip"
    )
    archive = env.path.tmp / f"llama-{normalized_build}-{uuid4().hex}.zip"
    extract_to = env.path.tmp / f"llama-{normalized_build}-{uuid4().hex}"

    print(f"Скачиваю llama.cpp {normalized_build}...")
    try:
        _download(
            archive_url,
            archive,
            label=f"llama.cpp {normalized_build}",
        )
        extract_to.mkdir(parents=True)
        with zipfile.ZipFile(archive) as package:
            package.extractall(extract_to)

        extracted_server = next(extract_to.rglob("llama-server.exe"), None)
        if extracted_server is None:
            raise FileNotFoundError("В архиве llama.cpp нет llama-server.exe")

        source = extracted_server.parent
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(
                "Каталог llama.cpp существует, но не содержит "
                f"llama-server.exe: {_display_zemi_path(target)}"
            )
        source.replace(target)
    finally:
        archive.unlink(missing_ok=True)
        if extract_to.exists():
            shutil.rmtree(extract_to)

    print(
        f"llama.cpp {normalized_build} скачан: "
        f"{_display_zemi_path(target)}"
    )
    return target


def download_model(
    owner: str,
    repository: str,
    filename: str,
    *,
    source: str = "hf",
    url: str | None = None,
) -> Path:
    """Скачивает GGUF-модель в каталог текущего ZEMI Instance."""
    target_directory = env.path.model(owner, repository, filename, source=source)
    target = target_directory / filename
    size_file = _model_size_file(target)

    if url is None:
        if source.removesuffix(":") != "hf":
            raise ValueError("Для источника, отличного от hf, необходимо передать url")
        url = f"https://huggingface.co/{owner}/{repository}/resolve/main/{filename}"

    if _is_complete_model(target, size_file):
        print(f"Модель уже скачана: {_display_zemi_path(target)}")
        return target

    print(f"Скачиваю модель {owner}/{repository}/{filename}...")
    _download(
        url,
        target,
        label=f"Модель {owner}/{repository}/{filename}",
        size_file=size_file,
    )
    print(f"Модель скачана: {_display_zemi_path(target)}")
    return target
