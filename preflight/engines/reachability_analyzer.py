"""Reachability Analyzer integration."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from botocore.exceptions import BotoCoreError, ClientError

from preflight.discovery import ResolvedTarget
from preflight.models import Assertion

ActualReachability = Literal["reachable", "not_reachable"]


@dataclass(slots=True)
class CleanupSummary:
    """Tracks cleanup attempts for temporary Reachability Analyzer artifacts."""

    analysis_delete_attempted: bool = False
    analysis_delete_succeeded: bool = False
    path_delete_attempted: bool = False
    path_delete_succeeded: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReachabilityAnalysisResult:
    """Final Reachability Analyzer result for one assertion."""

    actual_outcome: ActualReachability
    analysis_status: Literal["succeeded"]
    network_path_found: bool
    path_id: str
    analysis_id: str
    path_arn: str | None = None
    analysis_arn: str | None = None
    status_message: str | None = None
    explanation_code: str | None = None
    explanation_summary: str | None = None
    cleanup: CleanupSummary = field(default_factory=CleanupSummary)


class ReachabilityAnalyzerError(RuntimeError):
    """Raised for Reachability Analyzer execution failures."""

    def __init__(
        self,
        message: str,
        *,
        cleanup: CleanupSummary | None = None,
        path_id: str | None = None,
        analysis_id: str | None = None,
        analysis_status: str | None = None,
        status_message: str | None = None,
        explanation_code: str | None = None,
        explanation_summary: str | None = None,
    ) -> None:
        super().__init__(message)
        self.cleanup = cleanup or CleanupSummary()
        self.path_id = path_id
        self.analysis_id = analysis_id
        self.analysis_status = analysis_status
        self.status_message = status_message
        self.explanation_code = explanation_code
        self.explanation_summary = explanation_summary


def analyze_assertion(
    assertion: Assertion,
    source: ResolvedTarget,
    destination: ResolvedTarget,
    ec2_client: Any,
    execution_account_id: str,
    *,
    poll_interval_seconds: float = 1.0,
    max_polls: int = 60,
    sleeper: Callable[[float], None] = time.sleep,
) -> ReachabilityAnalysisResult:
    """Run Reachability Analyzer for one assertion.

    Normalize AWS SDK/service-call failures into ReachabilityAnalyzerError so the
    runner can report them as assertion-level execution errors. Unexpected
    programming mistakes should still surface normally.
    """

    if source.resolved_target_type != "eni" or destination.resolved_target_type != "eni":
        raise ReachabilityAnalyzerError(
            "Reachability Analyzer execution requires canonical ENI targets."
        )

    cleanup = CleanupSummary()
    path_id: str | None = None
    analysis_id: str | None = None

    try:
        path = _create_network_insights_path(
            ec2_client=ec2_client,
            assertion=assertion,
            source=source,
            destination=destination,
            execution_account_id=execution_account_id,
        )
        path_id = _require_str(
            path.get("NetworkInsightsPathId"),
            "Reachability Analyzer did not return a network insights path ID.",
        )

        analysis = _start_network_insights_analysis(
            ec2_client=ec2_client,
            path_id=path_id,
            source=source,
            destination=destination,
            execution_account_id=execution_account_id,
        )
        analysis_id = _require_str(
            analysis.get("NetworkInsightsAnalysisId"),
            "Reachability Analyzer did not return a network insights analysis ID.",
        )

        final_analysis = _wait_for_analysis_completion(
            ec2_client=ec2_client,
            analysis_id=analysis_id,
            poll_interval_seconds=poll_interval_seconds,
            max_polls=max_polls,
            sleeper=sleeper,
        )
        analysis_status = _require_str(
            final_analysis.get("Status"),
            f"Reachability Analyzer analysis '{analysis_id}' did not include a final status.",
        )
        explanation_code, explanation_summary = _summarize_explanations(final_analysis)
        status_message = _optional_str(final_analysis.get("StatusMessage"))

        if analysis_status != "succeeded":
            raise ReachabilityAnalyzerError(
                f"Reachability Analyzer analysis '{analysis_id}' finished with status "
                f"'{analysis_status}'.",
                cleanup=cleanup,
                path_id=path_id,
                analysis_id=analysis_id,
                analysis_status=analysis_status,
                status_message=status_message,
                explanation_code=explanation_code,
                explanation_summary=explanation_summary,
            )

        network_path_found = _require_bool(
            final_analysis.get("NetworkPathFound"),
            f"Reachability Analyzer analysis '{analysis_id}' did not include NetworkPathFound.",
        )
        actual_outcome: ActualReachability = "reachable" if network_path_found else "not_reachable"

        result = ReachabilityAnalysisResult(
            actual_outcome=actual_outcome,
            analysis_status="succeeded",
            network_path_found=network_path_found,
            path_id=path_id,
            analysis_id=analysis_id,
            path_arn=_optional_str(path.get("NetworkInsightsPathArn")),
            analysis_arn=_optional_str(final_analysis.get("NetworkInsightsAnalysisArn")),
            status_message=status_message,
            explanation_code=explanation_code,
            explanation_summary=explanation_summary,
            cleanup=cleanup,
        )
    except (ClientError, BotoCoreError) as exc:
        raise ReachabilityAnalyzerError(
            f"Reachability Analyzer AWS SDK error: {exc}",
            cleanup=cleanup,
            path_id=path_id,
            analysis_id=analysis_id,
        ) from exc
    except ReachabilityAnalyzerError:
        raise
    finally:
        cleanup = _cleanup_analysis_artifacts(
            ec2_client=ec2_client,
            cleanup=cleanup,
            path_id=path_id,
            analysis_id=analysis_id,
        )

    result.cleanup = cleanup
    return result


def _create_network_insights_path(
    ec2_client: Any,
    assertion: Assertion,
    source: ResolvedTarget,
    destination: ResolvedTarget,
    execution_account_id: str,
) -> dict[str, Any]:
    """Create a temporary network insights path."""

    response = ec2_client.create_network_insights_path(
        Source=_execution_endpoint_value(source, execution_account_id),
        Destination=_execution_endpoint_value(destination, execution_account_id),
        Protocol=assertion.protocol,
        DestinationPort=assertion.port,
    )
    path = response.get("NetworkInsightsPath")
    if not isinstance(path, dict):
        raise ReachabilityAnalyzerError(
            "Reachability Analyzer did not return a valid network insights path object."
        )
    return path


def _start_network_insights_analysis(
    ec2_client: Any,
    path_id: str,
    source: ResolvedTarget,
    destination: ResolvedTarget,
    execution_account_id: str,
) -> dict[str, Any]:
    """Start analysis for a temporary network insights path."""

    additional_accounts = _additional_accounts(
        source=source,
        destination=destination,
        execution_account_id=execution_account_id,
    )
    request_kwargs: dict[str, Any] = {
        "NetworkInsightsPathId": path_id,
    }
    if additional_accounts:
        request_kwargs["AdditionalAccounts"] = additional_accounts

    response = ec2_client.start_network_insights_analysis(**request_kwargs)
    analysis = response.get("NetworkInsightsAnalysis")
    if not isinstance(analysis, dict):
        raise ReachabilityAnalyzerError(
            "Reachability Analyzer did not return a valid network insights analysis object."
        )
    return analysis


def _wait_for_analysis_completion(
    ec2_client: Any,
    analysis_id: str,
    *,
    poll_interval_seconds: float,
    max_polls: int,
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    """Poll Reachability Analyzer until the analysis completes."""

    for attempt in range(max_polls):
        response = ec2_client.describe_network_insights_analyses(
            NetworkInsightsAnalysisIds=[analysis_id]
        )
        analyses = response.get("NetworkInsightsAnalyses")
        if not isinstance(analyses, list) or len(analyses) != 1:
            raise ReachabilityAnalyzerError(
                f"Expected exactly one analysis result for '{analysis_id}'."
            )

        analysis = analyses[0]
        if not isinstance(analysis, dict):
            raise ReachabilityAnalyzerError(
                f"Reachability Analyzer returned an invalid analysis object for '{analysis_id}'."
            )

        status = _require_str(
            analysis.get("Status"),
            f"Reachability Analyzer analysis '{analysis_id}' did not include a status.",
        )
        if status in {"succeeded", "failed"}:
            return analysis

        if attempt < max_polls - 1:
            sleeper(poll_interval_seconds)

    raise ReachabilityAnalyzerError(
        f"Timed out waiting for Reachability Analyzer analysis '{analysis_id}' to complete."
    )


def _cleanup_analysis_artifacts(
    ec2_client: Any,
    cleanup: CleanupSummary,
    *,
    path_id: str | None,
    analysis_id: str | None,
) -> CleanupSummary:
    """Attempt to delete temporary analysis artifacts."""

    if analysis_id is not None:
        cleanup.analysis_delete_attempted = True
        try:
            ec2_client.delete_network_insights_analysis(NetworkInsightsAnalysisId=analysis_id)
            cleanup.analysis_delete_succeeded = True
        except (ClientError, BotoCoreError) as exc:
            cleanup.errors.append(
                f"Failed to delete network insights analysis '{analysis_id}': {exc}"
            )

    if path_id is not None:
        cleanup.path_delete_attempted = True
        try:
            ec2_client.delete_network_insights_path(NetworkInsightsPathId=path_id)
            cleanup.path_delete_succeeded = True
        except (ClientError, BotoCoreError) as exc:
            cleanup.errors.append(f"Failed to delete network insights path '{path_id}': {exc}")

    return cleanup


def _execution_endpoint_value(target: ResolvedTarget, execution_account_id: str) -> str:
    """Return the ID or ARN Reachability Analyzer should use for a target."""

    owner_id = target.metadata.get("owner_id")
    if isinstance(owner_id, str) and owner_id != execution_account_id:
        return target.resolved_arn

    return target.resolved_identifier


def _additional_accounts(
    source: ResolvedTarget,
    destination: ResolvedTarget,
    execution_account_id: str,
) -> list[str]:
    """Return additional account IDs required for cross-account analysis."""

    account_ids = {
        owner_id
        for owner_id in (
            source.metadata.get("owner_id"),
            destination.metadata.get("owner_id"),
        )
        if isinstance(owner_id, str) and owner_id != execution_account_id
    }
    return sorted(account_ids)


def _summarize_explanations(analysis: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return a compact explanation code and summary."""

    explanations = analysis.get("Explanations")
    if not isinstance(explanations, list) or not explanations:
        return None, None

    first = explanations[0]
    if not isinstance(first, dict):
        return None, None

    explanation_code = _optional_str(first.get("ExplanationCode"))
    component_summary = _explanation_component_summary(first)
    if explanation_code is None:
        return None, component_summary
    if component_summary is None:
        return explanation_code, explanation_code
    return explanation_code, f"{explanation_code}: {component_summary}"


def _explanation_component_summary(explanation: dict[str, Any]) -> str | None:
    """Extract a small component summary from a Reachability Analyzer explanation."""

    for key in (
        "NetworkInterface",
        "SecurityGroup",
        "Subnet",
        "RouteTable",
        "InternetGateway",
        "NatGateway",
        "VpcEndpoint",
        "TransitGateway",
        "TransitGatewayAttachment",
        "Acl",
        "AclRule",
    ):
        raw_value = explanation.get(key)
        if not isinstance(raw_value, dict):
            continue
        for item_key, item_value in raw_value.items():
            if isinstance(item_value, str) and (
                item_key.endswith("Id") or item_key.endswith("Arn")
            ):
                return f"{key} {item_value}"

    port = explanation.get("Port")
    if isinstance(port, int):
        return f"port {port}"

    return None


def _require_str(value: Any, error_message: str) -> str:
    """Return a required string value or raise a clear execution error."""

    if isinstance(value, str) and value:
        return value

    raise ReachabilityAnalyzerError(error_message)


def _optional_str(value: Any) -> str | None:
    """Return a string value when present and valid."""

    if isinstance(value, str) and value:
        return value
    return None


def _require_bool(value: Any, error_message: str) -> bool:
    """Return a required bool value or raise a clear execution error."""

    if isinstance(value, bool):
        return value

    raise ReachabilityAnalyzerError(error_message)
