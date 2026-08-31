"""Public notebook helpers for structured ZEMI playbook output."""

from __future__ import annotations

import json
from typing import Any, Mapping

PLAYBOOK_OUTPUT_MIME = "application/vnd.zemi.playbook-output+json"
_published = False


def validate_output_params(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("output_params() requires a mapping")
    if not all(isinstance(key, str) for key in value):
        raise TypeError("output_params() mapping keys must be strings")
    try:
        return json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"output_params() requires finite JSON-serializable values: {error}") from error


def output_params(value: Mapping[str, Any]) -> None:
    """Publish one structured mapping with a standard notebook fallback."""
    global _published
    if _published:
        raise RuntimeError("output_params() may be called only once per notebook execution")
    normalized = validate_output_params(value)
    from IPython.display import display
    display(
        {
            PLAYBOOK_OUTPUT_MIME: normalized,
            "text/plain": json.dumps(normalized, ensure_ascii=False, indent=2),
        },
        raw=True,
    )
    _published = True


__all__ = ["PLAYBOOK_OUTPUT_MIME", "output_params"]
