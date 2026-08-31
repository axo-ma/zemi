"""Public ZEMI Arsenal lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .runtime import ArsenalSession


__all__ = ["begin", "end"]


def begin(
    config: str | Path | dict[str, Any] | ArsenalSession,
    *,
    stop_before_begin: bool,
) -> ArsenalSession:
    """Create and start an Arsenal session with lazy resource activation."""
    session = config if isinstance(config, ArsenalSession) else ArsenalSession(config)
    session._begin(
        stop_arsenal_before_begin=stop_before_begin,
    )
    return session


def end(session: ArsenalSession, *, stop_after_end: bool) -> None:
    """End a session and stop Arsenal when requested."""
    if not isinstance(session, ArsenalSession):
        raise TypeError("session must be an ArsenalSession instance")
    session._end(stop_arsenal_after_end=stop_after_end)
