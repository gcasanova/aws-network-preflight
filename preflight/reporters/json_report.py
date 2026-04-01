"""JSON serialization helpers for CI-friendly output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def render_json(payload: Mapping[str, Any]) -> str:
    """Return formatted JSON for a result payload."""

    return json.dumps(payload, indent=2, sort_keys=True)
