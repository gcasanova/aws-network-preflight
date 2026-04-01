"""Assertion orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from preflight.models import Assertion, PreflightConfig


@dataclass(slots=True)
class AssertionResult:
    """Result of running one assertion."""

    assertion_id: str
    assertion_type: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunSummary:
    """Aggregate results for a full config run."""

    results: list[AssertionResult]

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.results if not result.passed)


class AssertionNotFoundError(ValueError):
    """Raised when a named assertion does not exist in the loaded config."""


def find_assertion(config: PreflightConfig, assertion_id: str) -> Assertion:
    """Return one assertion by ID or raise a helpful error."""

    for assertion in config.assertions:
        if assertion.id == assertion_id:
            return assertion

    raise AssertionNotFoundError(f"Assertion '{assertion_id}' was not found in the config")


def run_assertions(_config: PreflightConfig, _profile_override: str | None = None) -> RunSummary:
    """Execute all assertions in a config."""

    raise NotImplementedError("Reachability Analyzer execution is planned for the next phase.")
