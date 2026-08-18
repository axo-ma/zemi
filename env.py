from __future__ import annotations

from pathlib import Path


INSTANCE_MARKERS = frozenset(
    {".zemiinst_dev", ".zemiinst_exp", ".zemiinst_prod"}
)
COMPONENT_MARKER = ".zemicomp"


def _path_part(value: str, name: str) -> str:
    """Validate a directory-name segment derived from an identifier."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"{name} must not contain path separators: {value!r}")
    return value


def _start_directory(start_path: str | Path | None) -> Path:
    path = Path.cwd() if start_path is None else Path(start_path)
    return path.resolve() if path.is_dir() else path.resolve().parent


class _Paths:
    """Dynamic paths for the current ZEMI Instance and Component."""

    @property
    def comp(self) -> Path:
        """Return the current ZEMI Component root identified by .zemicomp."""
        for directory in (_start_directory(None), *_start_directory(None).parents):
            if (directory / COMPONENT_MARKER).is_file():
                return directory
        raise FileNotFoundError("No ZEMI Component root with a .zemicomp marker was found")

    @property
    def inst(self) -> Path:
        """Return the current ZEMI Instance root identified by .zemiinst_*."""
        for directory in (_start_directory(None), *_start_directory(None).parents):
            if any((directory / marker).is_file() for marker in INSTANCE_MARKERS):
                return directory
        raise FileNotFoundError("No ZEMI Instance root with a .zemiinst_* marker was found")

    @property
    def tmp(self) -> Path:
        """Return the current ZEMI Instance _tmp service directory."""
        return self.inst / "_tmp"

    @property
    def llamas(self) -> Path:
        """Return the current ZEMI Instance _llamas directory."""
        return self.inst / "_llamas"

    @property
    def models(self) -> Path:
        """Return the current ZEMI Instance _models directory."""
        return self.inst / "_models"

    def llama(self, build: str) -> Path:
        """Return the path to a llama.cpp build such as ``b1234``."""
        build = build.removeprefix("llama:")
        return self.llamas / f"llama--{_path_part(build, 'build')}"

    def model(
        self,
        owner: str,
        repository: str,
        filename: str,
        *,
        source: str = "hf",
    ) -> Path:
        """Return the GGUF model directory for its compound identifier."""
        source = _path_part(source.removesuffix(":"), "source")
        owner = _path_part(owner, "owner")
        repository = _path_part(repository, "repository")
        filename = _path_part(filename, "filename")
        if not filename.lower().endswith(".gguf"):
            raise ValueError(f"filename must end with .gguf: {filename!r}")

        model_name = "--".join(
            (source, owner, repository, filename[:-len(".gguf")])
        )
        return self.models / model_name

    @property
    def pythons(self) -> Path:
        """Return the current ZEMI Instance _pythons directory."""
        return self.inst / "_pythons"


path = _Paths()
