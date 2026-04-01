"""Target resolution primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from preflight.models import Endpoint, PreflightConfig, Selector


@dataclass(slots=True)
class ResolvedTarget:
    """A uniquely resolved AWS target."""

    account: str
    region: str
    selector: Selector
    resource_id: str
    arn: str
    resource_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SelectorResolutionError(RuntimeError):
    """Raised when a selector cannot be resolved unambiguously."""


def resolve_target(_config: PreflightConfig, _endpoint: Endpoint) -> ResolvedTarget:
    """Resolve one endpoint into a concrete AWS target."""

    raise NotImplementedError(
        "Target resolution is planned for the next phase. "
        "Selectors will support resource_id, arn, and tags with exact-one-match enforcement."
    )
