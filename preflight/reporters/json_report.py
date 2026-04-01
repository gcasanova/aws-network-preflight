"""JSON serialization helpers for CI-friendly output."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from preflight.runner import RunSummary


def render_json(payload: Any) -> str:
    """Return formatted JSON for a result payload."""

    return json.dumps(_normalize_payload(payload), indent=2, sort_keys=True)


def _normalize_payload(payload: Any) -> Any:
    """Normalize supported result payloads for deterministic JSON serialization."""

    if isinstance(payload, RunSummary):
        return {
            "results": _normalize_payload(payload.results),
            "passed_count": payload.passed_count,
            "failed_count": payload.failed_count,
            "error_count": payload.error_count,
        }
    if is_dataclass(payload) and not isinstance(payload, type):
        return {
            field.name: _normalize_payload(getattr(payload, field.name))
            for field in fields(payload)
        }
    if isinstance(payload, BaseModel):
        return {
            key: _normalize_payload(value)
            for key, value in payload.model_dump(mode="python", exclude_none=True).items()
        }
    if isinstance(payload, dict):
        return {
            str(key): _normalize_payload(value)
            for key, value in sorted(payload.items(), key=lambda item: str(item[0]))
        }
    if isinstance(payload, (list, tuple)):
        return [_normalize_payload(item) for item in payload]
    if isinstance(payload, set):
        return [_normalize_payload(item) for item in sorted(payload, key=repr)]
    if isinstance(payload, Enum):
        return payload.value
    if isinstance(payload, Path):
        return str(payload)
    return payload
