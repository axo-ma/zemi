"""Generic interactive input and persistent-value store for a ZEMI Instance."""
from __future__ import annotations

import getpass
import os
import re
import subprocess
import time
import warnings
from pathlib import Path
from urllib.parse import urlsplit

from . import env

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InputError(ValueError):
    pass


def _decode(raw: str, line: int) -> str:
    value = raw.strip()
    if not value or value[0] not in "\"'":
        return value
    if len(value) < 2 or value[-1] != value[0]:
        raise InputError(f"persistent inputs line {line}: unterminated quoted value")
    body = value[1:-1]
    if value[0] == "'":
        return body.replace("\\'", "'").replace("\\\\", "\\")
    result, escaped = [], False
    mapping = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}
    for char in body:
        if escaped:
            if char not in mapping:
                raise InputError(f"persistent inputs line {line}: invalid escape")
            result.append(mapping[char]); escaped = False
        elif char == "\\":
            escaped = True
        else:
            result.append(char)
    if escaped:
        raise InputError(f"persistent inputs line {line}: invalid escape")
    return "".join(result)


def _encode(value: str) -> str:
    if value and re.fullmatch(r"[^\s#='\"\\]+", value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{escaped}"'


class InputStore:
    """Persistent values; never reads from or writes to ``os.environ``."""

    def __init__(self, path: Path | None = None, *, legacy_path: Path | None = None) -> None:
        self._path = path
        self._legacy_path = legacy_path if legacy_path is not None else (path if path is not None else None)

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else env.path.inst / "_inputs" / "values.env"

    @property
    def legacy_path(self) -> Path:
        return self._legacy_path if self._legacy_path is not None else env.path.inst / "_secrets" / "arsenal.env"

    @staticmethod
    def _read_path(path: Path) -> tuple[list[str], dict[str, str]]:
        if not path.exists():
            return [], {}
        lines = path.read_text(encoding="utf-8").splitlines(); values = {}
        for number, line in enumerate(lines, 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                raise InputError(f"persistent inputs line {number}: expected NAME=value")
            name, raw = line.split("=", 1); name = name.strip()
            if not _NAME.fullmatch(name):
                raise InputError(f"persistent inputs line {number}: invalid name {name!r}")
            if name in values:
                raise InputError(f"persistent inputs line {number}: duplicate {name!r}")
            values[name] = _decode(raw, number)
        return lines, values

    def _read(self) -> tuple[list[str], dict[str, str]]:
        return self._read_path(self.path)

    def get(self, name: str) -> str | None:
        if not _NAME.fullmatch(name):
            raise InputError(f"invalid persistent input name {name!r}")
        current = self._read()[1].get(name)
        if current is not None:
            return current
        legacy = self._read_path(self.legacy_path)[1].get(name) if self.legacy_path != self.path else None
        if legacy is not None:
            self.set(name, legacy)
        return legacy

    def set(self, name: str, value: str) -> None:
        if not _NAME.fullmatch(name):
            raise InputError(f"invalid persistent input name {name!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(".env.lock"); deadline = time.monotonic() + 10
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd); break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out locking {self.path}")
                time.sleep(.05)
        try:
            lines, _ = self._read(); replacement = f"{name}={_encode(value)}"; found = False
            for index, line in enumerate(lines):
                if "=" in line and line.split("=", 1)[0].strip() == name:
                    lines[index] = replacement; found = True
            if not found:
                lines.append(replacement)
            temporary = self.path.with_suffix(f".env.{os.getpid()}.tmp")
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o600); os.replace(temporary, self.path); self._acl()
        finally:
            try: lock.unlink()
            except FileNotFoundError: pass

    @staticmethod
    def valid(value: str, rule: str) -> bool:
        if rule == "non_empty": return bool(value.strip())
        if rule == "url":
            parts = urlsplit(value.strip())
            return parts.scheme in {"http", "https"} and bool(parts.hostname) and not any((parts.username, parts.password, parts.query, parts.fragment))
        if rule == "port": return value.isdigit() and 1 <= int(value) <= 65535
        raise InputError(f"unsupported validation rule {rule!r}")

    def resolve(self, specification: dict[str, object]) -> str:
        name = specification.get("env")
        rule = specification.get("validate")
        if rule is not None:
            rule = str(rule)
        if name is not None:
            name = str(name)
            current = self.get(name)
            if current is not None and (rule is None or self.valid(current, rule)):
                return current
        prompt = str(specification.get("prompt") or (f"Enter {name}" if name else "Enter a value"))
        suggested = specification.get("suggested", specification.get("default"))
        if suggested is not None:
            prompt += f" [{suggested}]"
        while True:
            try:
                answer = getpass.getpass(prompt + ": ") if specification.get("secret") is True else input(prompt + ": ")
            except (EOFError, KeyboardInterrupt) as error:
                raise InputError("interactive input was cancelled or unavailable") from error
            if not answer and suggested is not None:
                answer = str(suggested)
            if rule is None or self.valid(answer, rule):
                if name is not None:
                    self.set(name, answer)
                return answer
            print(f"Invalid value ({rule}); try again or press Ctrl+C to cancel.")

    def _acl(self) -> None:
        if os.name != "nt": return
        identity = subprocess.run(["whoami"], capture_output=True, text=True, check=False).stdout.strip()
        if not identity:
            warnings.warn(f"Could not restrict ACL for {self.path}"); return
        result = subprocess.run(["icacls", str(self.path), "/inheritance:r", "/grant:r", f"{identity}:(F)"], capture_output=True, check=False)
        if result.returncode:
            warnings.warn(f"Could not restrict ACL for {self.path}; protect it manually")


__all__ = ["InputError", "InputStore"]
