from __future__ import annotations

from pathlib import Path


INSTANCE_MARKERS = frozenset(
    {".zemiinst_dev", ".zemiinst_exp", ".zemiinst_prod"}
)
COMPONENT_MARKER = ".zemicomp"


def _path_part(value: str, name: str) -> str:
    """Проверяет часть имени каталога, полученную из идентификатора."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} должен быть непустой строкой")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"{name} не должен содержать разделители пути: {value!r}")
    return value


def _start_directory(start_path: str | Path | None) -> Path:
    path = Path.cwd() if start_path is None else Path(start_path)
    return path.resolve() if path.is_dir() else path.resolve().parent


class _Paths:
    """Динамические пути текущих ZEMI Instance и Component."""

    @property
    def comp(self) -> Path:
        """Корень текущего ZEMI Component по маркеру .zemicomp."""
        for directory in (_start_directory(None), *_start_directory(None).parents):
            if (directory / COMPONENT_MARKER).is_file():
                return directory
        raise FileNotFoundError("Не найден корень ZEMI Component с маркером .zemicomp")

    @property
    def inst(self) -> Path:
        """Корень текущего ZEMI Instance по маркеру .zemiinst_*."""
        for directory in (_start_directory(None), *_start_directory(None).parents):
            if any((directory / marker).is_file() for marker in INSTANCE_MARKERS):
                return directory
        raise FileNotFoundError("Не найден корень ZEMI Instance с маркером .zemiinst_*")

    @property
    def tmp(self) -> Path:
        """Служебная папка _tmp текущего ZEMI Instance."""
        return self.inst / "_tmp"

    @property
    def llamas(self) -> Path:
        """Папка _llamas текущего ZEMI Instance."""
        return self.inst / "_llamas"

    @property
    def models(self) -> Path:
        """Папка _models текущего ZEMI Instance."""
        return self.inst / "_models"

    def llama(self, build: str) -> Path:
        """Путь к сборке llama.cpp по версии, например ``b1234``."""
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
        """Путь к каталогу GGUF-модели по её составному идентификатору."""
        source = _path_part(source.removesuffix(":"), "source")
        owner = _path_part(owner, "owner")
        repository = _path_part(repository, "repository")
        filename = _path_part(filename, "filename")
        if not filename.lower().endswith(".gguf"):
            raise ValueError(f"filename должен оканчиваться на .gguf: {filename!r}")

        model_name = "--".join(
            (source, owner, repository, filename[:-len(".gguf")])
        )
        return self.models / model_name

    @property
    def pythons(self) -> Path:
        """Папка _pythons текущего ZEMI Instance."""
        return self.inst / "_pythons"


path = _Paths()
