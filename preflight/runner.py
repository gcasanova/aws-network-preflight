"""Assertion orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from preflight.auth import SessionFactory
from preflight.discovery import ResolvedAssertionTarget, ResolvedTarget, resolve_assertion_targets
from preflight.engines.reachability_analyzer import (
    CleanupSummary,
    ReachabilityAnalysisResult,
    ReachabilityAnalyzerError,
    analyze_assertion,
)
from preflight.models import Assertion, PreflightConfig

ExpectedOutcome = Literal["reachable", "not_reachable"]
ActualOutcome = Literal["reachable", "not_reachable", "error"]
AssertionStatus = Literal["passed", "failed", "error"]


@dataclass(slots=True)
class AssertionResult:
    """Result of running one assertion."""

    assertion_id: str
    assertion_type: Literal["allow", "deny"]
    expected_outcome: ExpectedOutcome
    actual_outcome: ActualOutcome
    status: AssertionStatus
    message: str
    source: ResolvedTarget
    destination: ResolvedTarget
    path_id: str | None = None
    analysis_id: str | None = None
    path_arn: str | None = None
    analysis_arn: str | None = None
    analysis_status: str | None = None
    network_path_found: bool | None = None
    status_message: str | None = None
    explanation_code: str | None = None
    explanation_summary: str | None = None
    cleanup: CleanupSummary = field(default_factory=CleanupSummary)


@dataclass(slots=True)
class RunSummary:
    """Aggregate results for a full config run."""

    results: list[AssertionResult]

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.status == "passed")

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.results if result.status == "failed")

    @property
    def error_count(self) -> int:
        return sum(1 for result in self.results if result.status == "error")


class AssertionNotFoundError(ValueError):
    """Raised when a named assertion does not exist in the loaded config."""


def find_assertion(config: PreflightConfig, assertion_id: str) -> Assertion:
    """Return one assertion by ID or raise a helpful error."""

    for assertion in config.assertions:
        if assertion.id == assertion_id:
            return assertion

    raise AssertionNotFoundError(f"Assertion '{assertion_id}' was not found in the config")


def run_assertions(
    config: PreflightConfig,
    profile_override: str | None = None,
    *,
    session_factory: SessionFactory | None = None,
    poll_interval_seconds: float = 1.0,
    max_polls: int = 60,
    sleeper: Callable[[float], None] = time.sleep,
) -> RunSummary:
    """Execute all assertions in a config."""

    if session_factory is None:
        session_factory = SessionFactory(
            config.defaults,
            config.accounts,
            profile_override=profile_override,
        )

    resolved_targets = resolve_assertion_targets(config, session_factory=session_factory)
    target_map = _resolved_target_map(resolved_targets)
    results = [
        _run_resolved_assertion(
            assertion=assertion,
            source=target_map[(assertion.id, "source")],
            destination=target_map[(assertion.id, "destination")],
            session_factory=session_factory,
            poll_interval_seconds=poll_interval_seconds,
            max_polls=max_polls,
            sleeper=sleeper,
        )
        for assertion in config.assertions
    ]
    return RunSummary(results=results)


def run_assertion(
    config: PreflightConfig,
    assertion_id: str,
    profile_override: str | None = None,
    *,
    session_factory: SessionFactory | None = None,
    poll_interval_seconds: float = 1.0,
    max_polls: int = 60,
    sleeper: Callable[[float], None] = time.sleep,
) -> AssertionResult:
    """Execute one assertion by ID."""

    assertion = find_assertion(config, assertion_id)
    single_assertion_config = config.model_copy(update={"assertions": [assertion]})
    if session_factory is None:
        session_factory = SessionFactory(
            config.defaults,
            config.accounts,
            profile_override=profile_override,
        )

    resolved_targets = resolve_assertion_targets(
        single_assertion_config,
        session_factory=session_factory,
    )
    target_map = _resolved_target_map(resolved_targets)
    return _run_resolved_assertion(
        assertion=assertion,
        source=target_map[(assertion.id, "source")],
        destination=target_map[(assertion.id, "destination")],
        session_factory=session_factory,
        poll_interval_seconds=poll_interval_seconds,
        max_polls=max_polls,
        sleeper=sleeper,
    )


def _run_resolved_assertion(
    assertion: Assertion,
    source: ResolvedTarget,
    destination: ResolvedTarget,
    session_factory: SessionFactory,
    *,
    poll_interval_seconds: float,
    max_polls: int,
    sleeper: Callable[[float], None],
) -> AssertionResult:
    """Execute one already-resolved assertion."""

    execution_account = assertion.source.account
    session = session_factory.session_for_account(execution_account, region=source.region)
    execution_account_id = session_factory.account_id_for_account(
        execution_account,
        region=source.region,
    )
    ec2_client = session.client("ec2")
    expected_outcome = _expected_outcome(assertion)

    try:
        analysis_result = analyze_assertion(
            assertion=assertion,
            source=source,
            destination=destination,
            ec2_client=ec2_client,
            execution_account_id=execution_account_id,
            poll_interval_seconds=poll_interval_seconds,
            max_polls=max_polls,
            sleeper=sleeper,
        )
    except ReachabilityAnalyzerError as exc:
        return AssertionResult(
            assertion_id=assertion.id,
            assertion_type=assertion.type,
            expected_outcome=expected_outcome,
            actual_outcome="error",
            status="error",
            message=str(exc),
            source=source,
            destination=destination,
            path_id=exc.path_id,
            analysis_id=exc.analysis_id,
            analysis_status=exc.analysis_status,
            status_message=exc.status_message,
            explanation_code=exc.explanation_code,
            explanation_summary=exc.explanation_summary,
            cleanup=exc.cleanup,
        )

    if analysis_result.cleanup.errors:
        cleanup_message = "; ".join(analysis_result.cleanup.errors)
        return _assertion_result_from_analysis(
            assertion=assertion,
            source=source,
            destination=destination,
            expected_outcome=expected_outcome,
            analysis_result=analysis_result,
            status="error",
            message=f"Cleanup failed after analysis: {cleanup_message}",
        )

    if analysis_result.actual_outcome == expected_outcome:
        status: AssertionStatus = "passed"
        message = (
            f"Expected {humanize_outcome(expected_outcome)} and Reachability Analyzer reported "
            f"{humanize_outcome(analysis_result.actual_outcome)}."
        )
    else:
        status = "failed"
        message = (
            f"Expected {humanize_outcome(expected_outcome)} but Reachability Analyzer reported "
            f"{humanize_outcome(analysis_result.actual_outcome)}."
        )

    return _assertion_result_from_analysis(
        assertion=assertion,
        source=source,
        destination=destination,
        expected_outcome=expected_outcome,
        analysis_result=analysis_result,
        status=status,
        message=message,
    )


def _assertion_result_from_analysis(
    assertion: Assertion,
    source: ResolvedTarget,
    destination: ResolvedTarget,
    expected_outcome: ExpectedOutcome,
    analysis_result: ReachabilityAnalysisResult,
    *,
    status: AssertionStatus,
    message: str,
) -> AssertionResult:
    """Build an assertion result from a successful analysis object."""

    return AssertionResult(
        assertion_id=assertion.id,
        assertion_type=assertion.type,
        expected_outcome=expected_outcome,
        actual_outcome=analysis_result.actual_outcome,
        status=status,
        message=message,
        source=source,
        destination=destination,
        path_id=analysis_result.path_id,
        analysis_id=analysis_result.analysis_id,
        path_arn=analysis_result.path_arn,
        analysis_arn=analysis_result.analysis_arn,
        analysis_status=analysis_result.analysis_status,
        network_path_found=analysis_result.network_path_found,
        status_message=analysis_result.status_message,
        explanation_code=analysis_result.explanation_code,
        explanation_summary=analysis_result.explanation_summary,
        cleanup=analysis_result.cleanup,
    )


def _resolved_target_map(
    resolved_targets: list[ResolvedAssertionTarget],
) -> dict[tuple[str, str], ResolvedTarget]:
    """Index resolved targets by assertion ID and endpoint role."""

    return {(item.assertion_id, item.endpoint_role): item.target for item in resolved_targets}


def _expected_outcome(assertion: Assertion) -> ExpectedOutcome:
    """Map assertion type to the expected reachability outcome."""

    if assertion.type == "allow":
        return "reachable"
    return "not_reachable"


def humanize_outcome(outcome: ActualOutcome | ExpectedOutcome) -> str:
    """Render an outcome value for human-readable output."""

    if outcome == "not_reachable":
        return "not reachable"
    return outcome
