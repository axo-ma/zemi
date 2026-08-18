"""Download llama.cpp and GGUF models for ZEMI Arsenal."""

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
    """Expected failure while downloading an external resource."""


def _display_zemi_path(path: Path) -> str:
    """Represent a file path through a ZEMI marker."""
    path = path.resolve()
    for marker, root in (("@comp", env.path.comp), ("@inst", env.path.inst)):
        try:
            relative = path.relative_to(root.resolve())
        except ValueError:
            continue
        return f"{marker}/{relative.as_posix()}"
    raise ValueError(f"Path is outside the ZEMI Instance: {path.name}")


def _format_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size:.0f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


class _Progress:
    """Update one terminal line and one Jupyter display area consistently."""

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
        speed = f"{_format_size(loaded / elapsed)}/s"
        if self.total:
            percent = min(loaded / self.total * 100, 100.0)
            amount = f"{_format_size(loaded)} / {_format_size(self.total)}"
            message = f"{self.label}: {percent:5.1f}% · {amount} · {speed}"
        else:
            message = f"{self.label}: {_format_size(loaded)} · {speed}"

        if done:
            message += " · complete"

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
    """Stream a URL with progress and atomically move the file into place."""
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
                        "The server did not report the model size.\n"
                        f"URL: {url}\n"
                        "The model cannot be downloaded safely without its size."
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
                    "The download ended prematurely.\n"
                    f"Received: {_format_size(loaded)}\n"
                    f"Expected: {_format_size(total)}\n"
                    f"URL: {url}\n"
                    "The temporary file will be removed; retry the download."
                )

            progress.update(loaded, done=True)
        temporary.replace(destination)
    except HTTPError as error:
        temporary.unlink(missing_ok=True)
        reason = error.reason or "no explanation provided"
        raise DownloadError(
            "The server did not return the requested file.\n"
            f"HTTP status: {error.code} {reason}\n"
            f"URL: {url}\n"
            "Check the repository name, file name, and release availability."
        ) from None
    except URLError as error:
        temporary.unlink(missing_ok=True)
        raise DownloadError(
            "Could not connect to the download server.\n"
            f"Reason: {error.reason}\n"
            f"URL: {url}\n"
            "Check the internet connection and site availability."
        ) from None
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise DownloadError(
            "Could not save the downloaded file.\n"
            f"Destination: {_display_zemi_path(destination)}\n"
            f"Reason: {error}"
        ) from None
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _model_size_file(model_path: Path) -> Path:
    """Return the path to the file storing the expected model size."""
    return model_path.with_name(f"{model_path.name}.size")


def _read_model_size(size_file: Path) -> int | None:
    """Read the stored model size or return ``None``."""
    try:
        value = size_file.read_text(encoding="ascii").strip()
    except (FileNotFoundError, OSError, UnicodeError):
        return None
    return int(value) if value.isdigit() and int(value) > 0 else None


def _is_complete_model(model_path: Path, size_file: Path) -> bool:
    """Validate a model against the locally stored expected size."""
    if not model_path.is_file():
        return False

    expected_size = _read_model_size(size_file)
    actual_size = model_path.stat().st_size

    if expected_size is None:
        size_file.write_text(str(actual_size), encoding="ascii")
        print(
            "Created a local size file for an existing downloaded model: "
            f"{_display_zemi_path(size_file)}"
        )
        return True

    if actual_size == expected_size:
        return True

    print(
        "An incomplete model file was found:\n"
        f"  file: {_display_zemi_path(model_path)}\n"
        f"  downloaded: {_format_size(actual_size)}\n"
        f"  expected: {_format_size(expected_size)}\n"
        "The model will be downloaded again."
    )
    return False


def download_llama(build: str, *, url: str | None = None) -> Path:
    """Download and extract a Windows CPU llama.cpp build into the ZEMI Instance."""
    normalized_build = build.removeprefix("llama:")
    target = env.path.llama(normalized_build)
    server = target / "llama-server.exe"

    if server.is_file():
        print(
            f"llama.cpp {normalized_build} is already downloaded: "
            f"{_display_zemi_path(target)}"
        )
        return target

    archive_url = url or (
        "https://github.com/ggml-org/llama.cpp/releases/download/"
        f"{normalized_build}/llama-{normalized_build}-bin-win-cpu-x64.zip"
    )
    archive = env.path.tmp / f"llama-{normalized_build}-{uuid4().hex}.zip"
    extract_to = env.path.tmp / f"llama-{normalized_build}-{uuid4().hex}"

    print(f"Downloading llama.cpp {normalized_build}...")
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
            raise FileNotFoundError("The llama.cpp archive does not contain llama-server.exe")

        source = extracted_server.parent
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(
                "The llama.cpp directory exists but does not contain "
                f"llama-server.exe: {_display_zemi_path(target)}"
            )
        source.replace(target)
    finally:
        archive.unlink(missing_ok=True)
        if extract_to.exists():
            shutil.rmtree(extract_to)

    print(
        f"llama.cpp {normalized_build} downloaded: "
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
    """Download a GGUF model into the current ZEMI Instance directory."""
    target_directory = env.path.model(owner, repository, filename, source=source)
    target = target_directory / filename
    size_file = _model_size_file(target)

    if url is None:
        if source.removesuffix(":") != "hf":
            raise ValueError("url is required for a source other than hf")
        url = f"https://huggingface.co/{owner}/{repository}/resolve/main/{filename}"

    if _is_complete_model(target, size_file):
        print(f"Model is already downloaded: {_display_zemi_path(target)}")
        return target

    print(f"Downloading model {owner}/{repository}/{filename}...")
    _download(
        url,
        target,
        label=f"Model {owner}/{repository}/{filename}",
        size_file=size_file,
    )
    print(f"Model downloaded: {_display_zemi_path(target)}")
    return target
