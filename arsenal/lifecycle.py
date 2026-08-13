"""Публичный жизненный цикл ZEMI Arsenal."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .runtime import ArsenalSession


__all__ = ["begin", "end"]


def begin(
    config: str | Path | dict[str, Any] | ArsenalSession,
    *,
    stop_before_begin: bool,
    llama_router_mode: bool = False,
) -> ArsenalSession:
    """Создаёт и запускает сессию Arsenal с ленивой активацией ресурсов."""
    session = config if isinstance(config, ArsenalSession) else ArsenalSession(config)
    session._begin(
        stop_arsenal_before_begin=stop_before_begin,
        llama_router_mode=llama_router_mode,
    )
    return session


def end(session: ArsenalSession, *, stop_after_end: bool) -> None:
    """Завершает сессию и при необходимости останавливает Arsenal."""
    if not isinstance(session, ArsenalSession):
        raise TypeError("session должен быть экземпляром ArsenalSession")
    session._end(stop_arsenal_after_end=stop_after_end)
