"""JSON serialization helpers for CI-friendly output."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, cast


def render_json(payload: Any) -> str:
    """Return formatted JSON for a result payload."""

    return json.dumps(_normalize_payload(payload), indent=2, sort_keys=True)


def _normalize_payload(payload: Any) -> Any:
    """Normalize dataclass payloads for JSON serialization."""

    if is_dataclass(payload) and not isinstance(payload, type):
        return asdict(cast(Any, payload))
    return payload
