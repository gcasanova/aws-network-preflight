"""Reachability Analyzer integration."""

from __future__ import annotations

from typing import Any

from preflight.discovery import ResolvedTarget
from preflight.models import Assertion


class ReachabilityAnalyzerError(RuntimeError):
    """Raised for Reachability Analyzer execution failures."""


def analyze_assertion(
    _assertion: Assertion,
    _source: ResolvedTarget,
    _destination: ResolvedTarget,
    _session: Any,
) -> dict[str, Any]:
    """Run Reachability Analyzer for one assertion."""

    raise NotImplementedError("Reachability Analyzer execution is planned for the next phase.")
