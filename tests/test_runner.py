from __future__ import annotations

from typing import Any

import pytest
from botocore.stub import Stubber

from preflight.discovery import ResolvedAssertionTarget, ResolvedTarget
from preflight.engines.reachability_analyzer import (
    CleanupSummary,
    ReachabilityAnalysisResult,
    ReachabilityAnalyzerError,
)
from preflight.models import PreflightConfig, Selector
from preflight.runner import run_assertion, run_assertions
from tests.fakes import boto3_client


class StaticSession:
    def __init__(self, ec2_client: object) -> None:
        self._ec2_client = ec2_client

    def client(self, service_name: str) -> object:
        if service_name != "ec2":
            raise AssertionError(f"Unexpected service request: {service_name}")
        return self._ec2_client


class StaticSessionFactory:
    def __init__(self, ec2_client: object, *, account_id: str = "222222222222") -> None:
        self._ec2_client = ec2_client
        self._account_id = account_id

    def session_for_account(self, account_name: str, region: str | None = None) -> StaticSession:
        _ = (account_name, region)
        return StaticSession(self._ec2_client)

    def account_id_for_account(self, account_name: str, region: str | None = None) -> str:
        _ = (account_name, region)
        return self._account_id


def build_config(*, assertion_type: str = "allow") -> PreflightConfig:
    return PreflightConfig.model_validate(
        {
            "version": 1,
            "defaults": {"region": "us-east-1"},
            "accounts": {"app": {"regions": ["us-east-1"]}},
            "assertions": [
                {
                    "id": "assertion-1",
                    "type": assertion_type,
                    "source": {
                        "account": "app",
                        "selector": {"resource_id": "eni-0123456789abcdef0"},
                    },
                    "destination": {
                        "account": "app",
                        "selector": {"resource_id": "eni-0fedcba9876543210"},
                    },
                    "protocol": "tcp",
                    "port": 443,
                }
            ],
        }
    )


def build_target(eni_id: str) -> ResolvedTarget:
    return ResolvedTarget(
        account="app",
        region="us-east-1",
        selector=Selector(resource_id=eni_id),
        selector_type="resource_id",
        input_type="eni",
        input_identifier=eni_id,
        input_arn=f"arn:aws:ec2:us-east-1:222222222222:network-interface/{eni_id}",
        resolved_target_type="eni",
        resolved_identifier=eni_id,
        resolved_arn=f"arn:aws:ec2:us-east-1:222222222222:network-interface/{eni_id}",
        normalized=False,
        metadata={"owner_id": "222222222222"},
    )


def build_resolved_targets() -> list[ResolvedAssertionTarget]:
    return [
        ResolvedAssertionTarget(
            assertion_id="assertion-1",
            endpoint_role="source",
            target=build_target("eni-0123456789abcdef0"),
        ),
        ResolvedAssertionTarget(
            assertion_id="assertion-1",
            endpoint_role="destination",
            target=build_target("eni-0fedcba9876543210"),
        ),
    ]


def build_analysis_result(actual_outcome: str) -> ReachabilityAnalysisResult:
    return ReachabilityAnalysisResult(
        actual_outcome=actual_outcome,  # type: ignore[arg-type]
        analysis_status="succeeded",
        network_path_found=actual_outcome == "reachable",
        path_id="nip-1234567890abcdef0",
        analysis_id="nia-1234567890abcdef0",
    )


def add_successful_analysis_flow(
    stubber: Stubber,
    *,
    network_path_found: bool,
) -> None:
    stubber.add_response(
        "create_network_insights_path",
        {
            "NetworkInsightsPath": {
                "NetworkInsightsPathId": "nip-1234567890abcdef0",
            }
        },
        {
            "Source": "eni-0123456789abcdef0",
            "Destination": "eni-0fedcba9876543210",
            "Protocol": "tcp",
            "DestinationPort": 443,
        },
    )
    stubber.add_response(
        "start_network_insights_analysis",
        {
            "NetworkInsightsAnalysis": {
                "NetworkInsightsAnalysisId": "nia-1234567890abcdef0",
            }
        },
        {"NetworkInsightsPathId": "nip-1234567890abcdef0"},
    )
    stubber.add_response(
        "describe_network_insights_analyses",
        {
            "NetworkInsightsAnalyses": [
                {
                    "NetworkInsightsAnalysisId": "nia-1234567890abcdef0",
                    "Status": "succeeded",
                    "NetworkPathFound": network_path_found,
                }
            ]
        },
        {"NetworkInsightsAnalysisIds": ["nia-1234567890abcdef0"]},
    )
    stubber.add_response(
        "delete_network_insights_analysis",
        {"NetworkInsightsAnalysisId": "nia-1234567890abcdef0"},
        {"NetworkInsightsAnalysisId": "nia-1234567890abcdef0"},
    )
    stubber.add_response(
        "delete_network_insights_path",
        {"NetworkInsightsPathId": "nip-1234567890abcdef0"},
        {"NetworkInsightsPathId": "nip-1234567890abcdef0"},
    )


def test_allow_assertion_passes_when_analysis_is_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_config(assertion_type="allow")
    monkeypatch.setattr(
        "preflight.runner.resolve_assertion_targets",
        lambda *args, **kwargs: build_resolved_targets(),
    )
    monkeypatch.setattr(
        "preflight.runner.analyze_assertion",
        lambda *args, **kwargs: build_analysis_result("reachable"),
    )

    summary = run_assertions(config, session_factory=StaticSessionFactory(object()))

    assert len(summary.results) == 1
    assert summary.results[0].status == "passed"
    assert summary.results[0].actual_outcome == "reachable"


def test_allow_assertion_fails_when_analysis_is_not_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_config(assertion_type="allow")
    monkeypatch.setattr(
        "preflight.runner.resolve_assertion_targets",
        lambda *args, **kwargs: build_resolved_targets(),
    )
    monkeypatch.setattr(
        "preflight.runner.analyze_assertion",
        lambda *args, **kwargs: build_analysis_result("not_reachable"),
    )

    summary = run_assertions(config, session_factory=StaticSessionFactory(object()))

    assert summary.results[0].status == "failed"
    assert summary.results[0].expected_outcome == "reachable"
    assert summary.results[0].actual_outcome == "not_reachable"


def test_deny_assertion_passes_when_analysis_is_not_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_config(assertion_type="deny")
    monkeypatch.setattr(
        "preflight.runner.resolve_assertion_targets",
        lambda *args, **kwargs: build_resolved_targets(),
    )
    monkeypatch.setattr(
        "preflight.runner.analyze_assertion",
        lambda *args, **kwargs: build_analysis_result("not_reachable"),
    )

    summary = run_assertions(config, session_factory=StaticSessionFactory(object()))

    assert summary.results[0].status == "passed"
    assert summary.results[0].expected_outcome == "not_reachable"


def test_deny_assertion_fails_when_analysis_is_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_config(assertion_type="deny")
    monkeypatch.setattr(
        "preflight.runner.resolve_assertion_targets",
        lambda *args, **kwargs: build_resolved_targets(),
    )
    monkeypatch.setattr(
        "preflight.runner.analyze_assertion",
        lambda *args, **kwargs: build_analysis_result("reachable"),
    )

    summary = run_assertions(config, session_factory=StaticSessionFactory(object()))

    assert summary.results[0].status == "failed"
    assert summary.results[0].actual_outcome == "reachable"


def test_analysis_execution_error_is_reported_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_config(assertion_type="allow")
    monkeypatch.setattr(
        "preflight.runner.resolve_assertion_targets",
        lambda *args, **kwargs: build_resolved_targets(),
    )

    def raise_error(*args: Any, **kwargs: Any) -> ReachabilityAnalysisResult:
        raise ReachabilityAnalyzerError(
            "analysis failed",
            path_id="nip-1234567890abcdef0",
            analysis_id="nia-1234567890abcdef0",
        )

    monkeypatch.setattr("preflight.runner.analyze_assertion", raise_error)

    summary = run_assertions(config, session_factory=StaticSessionFactory(object()))

    assert summary.results[0].status == "error"
    assert summary.results[0].actual_outcome == "error"
    assert summary.results[0].analysis_id == "nia-1234567890abcdef0"


def test_run_assertions_interprets_stubbed_not_reachable_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_config(assertion_type="deny")
    monkeypatch.setattr(
        "preflight.runner.resolve_assertion_targets",
        lambda *args, **kwargs: build_resolved_targets(),
    )
    ec2_client = boto3_client("ec2")

    with Stubber(ec2_client) as stubber:
        add_successful_analysis_flow(stubber, network_path_found=False)
        result = run_assertions(
            config,
            session_factory=StaticSessionFactory(ec2_client),
            poll_interval_seconds=0.0,
        ).results[0]
        stubber.assert_no_pending_responses()

    assert result.status == "passed"
    assert result.expected_outcome == "not_reachable"
    assert result.actual_outcome == "not_reachable"
    assert result.analysis_status == "succeeded"


def test_run_assertions_reports_failed_analysis_status_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_config(assertion_type="allow")
    monkeypatch.setattr(
        "preflight.runner.resolve_assertion_targets",
        lambda *args, **kwargs: build_resolved_targets(),
    )
    ec2_client = boto3_client("ec2")

    with Stubber(ec2_client) as stubber:
        stubber.add_response(
            "create_network_insights_path",
            {"NetworkInsightsPath": {"NetworkInsightsPathId": "nip-1234567890abcdef0"}},
            {
                "Source": "eni-0123456789abcdef0",
                "Destination": "eni-0fedcba9876543210",
                "Protocol": "tcp",
                "DestinationPort": 443,
            },
        )
        stubber.add_response(
            "start_network_insights_analysis",
            {
                "NetworkInsightsAnalysis": {
                    "NetworkInsightsAnalysisId": "nia-1234567890abcdef0",
                }
            },
            {"NetworkInsightsPathId": "nip-1234567890abcdef0"},
        )
        stubber.add_response(
            "describe_network_insights_analyses",
            {
                "NetworkInsightsAnalyses": [
                    {
                        "NetworkInsightsAnalysisId": "nia-1234567890abcdef0",
                        "Status": "failed",
                        "StatusMessage": "analysis failed",
                        "Explanations": [{"ExplanationCode": "UNKNOWN", "Port": 443}],
                    }
                ]
            },
            {"NetworkInsightsAnalysisIds": ["nia-1234567890abcdef0"]},
        )
        stubber.add_response(
            "delete_network_insights_analysis",
            {"NetworkInsightsAnalysisId": "nia-1234567890abcdef0"},
            {"NetworkInsightsAnalysisId": "nia-1234567890abcdef0"},
        )
        stubber.add_response(
            "delete_network_insights_path",
            {"NetworkInsightsPathId": "nip-1234567890abcdef0"},
            {"NetworkInsightsPathId": "nip-1234567890abcdef0"},
        )

        result = run_assertions(
            config,
            session_factory=StaticSessionFactory(ec2_client),
            poll_interval_seconds=0.0,
        ).results[0]
        stubber.assert_no_pending_responses()

    assert result.status == "error"
    assert result.actual_outcome == "error"
    assert result.analysis_status == "failed"
    assert result.status_message == "analysis failed"
    assert result.explanation_summary == "UNKNOWN: port 443"


def test_cleanup_failure_turns_otherwise_successful_analysis_into_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_config(assertion_type="allow")
    monkeypatch.setattr(
        "preflight.runner.resolve_assertion_targets",
        lambda *args, **kwargs: build_resolved_targets(),
    )
    monkeypatch.setattr(
        "preflight.runner.analyze_assertion",
        lambda *args, **kwargs: ReachabilityAnalysisResult(
            actual_outcome="reachable",
            analysis_status="succeeded",
            network_path_found=True,
            path_id="nip-1234567890abcdef0",
            analysis_id="nia-1234567890abcdef0",
            cleanup=CleanupSummary(
                analysis_delete_attempted=True,
                path_delete_attempted=True,
                errors=["delete failed"],
            ),
        ),
    )

    result = run_assertions(config, session_factory=StaticSessionFactory(object())).results[0]

    assert result.status == "error"
    assert result.actual_outcome == "reachable"
    assert "Cleanup failed after analysis" in result.message


def test_run_assertion_resolves_and_executes_only_requested_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_config(assertion_type="allow")
    observed_assertion_ids: list[str] = []

    def resolve_single(config: PreflightConfig, **kwargs: Any) -> list[ResolvedAssertionTarget]:
        observed_assertion_ids.extend(assertion.id for assertion in config.assertions)
        return build_resolved_targets()

    monkeypatch.setattr("preflight.runner.resolve_assertion_targets", resolve_single)
    monkeypatch.setattr(
        "preflight.runner.analyze_assertion",
        lambda *args, **kwargs: build_analysis_result("reachable"),
    )

    result = run_assertion(
        config,
        "assertion-1",
        session_factory=StaticSessionFactory(object()),
    )

    assert observed_assertion_ids == ["assertion-1"]
    assert result.status == "passed"
