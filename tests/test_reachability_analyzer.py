from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from preflight.discovery import ResolvedTarget
from preflight.engines.reachability_analyzer import (
    ReachabilityAnalyzerError,
    analyze_assertion,
)
from preflight.models import Assertion, Endpoint, Selector
from tests.fakes import FakeReachabilityAnalyzerClient


def build_assertion(assertion_type: str = "allow") -> Assertion:
    return Assertion(
        id="assertion-1",
        type=assertion_type,  # type: ignore[arg-type]
        source=Endpoint(account="app", selector=Selector(resource_id="eni-0123456789abcdef0")),
        destination=Endpoint(account="app", selector=Selector(resource_id="eni-0fedcba9876543210")),
        protocol="tcp",
        port=443,
    )


def build_target(eni_id: str, *, owner_id: str = "222222222222") -> ResolvedTarget:
    return ResolvedTarget(
        account="app",
        region="us-east-1",
        selector=Selector(resource_id=eni_id),
        selector_type="resource_id",
        input_type="eni",
        input_identifier=eni_id,
        input_arn=f"arn:aws:ec2:us-east-1:{owner_id}:network-interface/{eni_id}",
        resolved_target_type="eni",
        resolved_identifier=eni_id,
        resolved_arn=f"arn:aws:ec2:us-east-1:{owner_id}:network-interface/{eni_id}",
        normalized=False,
        metadata={"owner_id": owner_id},
    )


def client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


def test_analyze_assertion_returns_reachable_result() -> None:
    client = FakeReachabilityAnalyzerClient(
        analysis_sequence=[
            {
                "NetworkInsightsAnalysisId": "nia-1",
                "NetworkInsightsAnalysisArn": (
                    "arn:aws:ec2:us-east-1:222222222222:network-insights-analysis/nia-1"
                ),
                "Status": "succeeded",
                "NetworkPathFound": True,
                "Explanations": [{"ExplanationCode": "ENI_SG_RULES_MISMATCH"}],
            }
        ]
    )

    result = analyze_assertion(
        build_assertion("allow"),
        build_target("eni-0123456789abcdef0"),
        build_target("eni-0fedcba9876543210"),
        client,
        "222222222222",
        poll_interval_seconds=0.0,
    )

    assert result.actual_outcome == "reachable"
    assert result.analysis_status == "succeeded"
    assert result.cleanup.analysis_delete_succeeded is True
    assert result.cleanup.path_delete_succeeded is True


def test_analyze_assertion_returns_not_reachable_result() -> None:
    client = FakeReachabilityAnalyzerClient(
        analysis_sequence=[
            {
                "NetworkInsightsAnalysisId": "nia-1",
                "Status": "succeeded",
                "NetworkPathFound": False,
                "Explanations": [{"ExplanationCode": "SECURITY_GROUP_RULE_MISMATCH"}],
            }
        ]
    )

    result = analyze_assertion(
        build_assertion("deny"),
        build_target("eni-0123456789abcdef0"),
        build_target("eni-0fedcba9876543210"),
        client,
        "222222222222",
        poll_interval_seconds=0.0,
    )

    assert result.actual_outcome == "not_reachable"


def test_analyze_assertion_raises_when_analysis_status_fails() -> None:
    client = FakeReachabilityAnalyzerClient(
        analysis_sequence=[
            {
                "NetworkInsightsAnalysisId": "nia-1",
                "Status": "failed",
                "StatusMessage": "analysis failed",
                "Explanations": [{"ExplanationCode": "UNKNOWN"}],
            }
        ]
    )

    with pytest.raises(ReachabilityAnalyzerError, match="finished with status 'failed'"):
        analyze_assertion(
            build_assertion("allow"),
            build_target("eni-0123456789abcdef0"),
            build_target("eni-0fedcba9876543210"),
            client,
            "222222222222",
            poll_interval_seconds=0.0,
        )

    assert client.deleted_analysis_ids == ["nia-1234567890abcdef0"]
    assert client.deleted_path_ids == ["nip-1234567890abcdef0"]


def test_analyze_assertion_raises_when_path_creation_fails() -> None:
    client = FakeReachabilityAnalyzerClient(
        create_path_error=client_error("UnauthorizedOperation", "CreateNetworkInsightsPath")
    )

    with pytest.raises(ReachabilityAnalyzerError, match="Reachability Analyzer API error"):
        analyze_assertion(
            build_assertion("allow"),
            build_target("eni-0123456789abcdef0"),
            build_target("eni-0fedcba9876543210"),
            client,
            "222222222222",
            poll_interval_seconds=0.0,
        )

    assert client.deleted_analysis_ids == []
    assert client.deleted_path_ids == []


def test_analyze_assertion_polls_until_completion() -> None:
    sleeps: list[float] = []
    client = FakeReachabilityAnalyzerClient(
        analysis_sequence=[
            {"NetworkInsightsAnalysisId": "nia-1", "Status": "running"},
            {"NetworkInsightsAnalysisId": "nia-1", "Status": "succeeded", "NetworkPathFound": True},
        ]
    )

    analyze_assertion(
        build_assertion("allow"),
        build_target("eni-0123456789abcdef0"),
        build_target("eni-0fedcba9876543210"),
        client,
        "222222222222",
        poll_interval_seconds=0.5,
        sleeper=sleeps.append,
    )

    assert len(client.describe_analysis_calls) == 2
    assert sleeps == [0.5]


def test_analyze_assertion_attempts_cleanup_after_success() -> None:
    client = FakeReachabilityAnalyzerClient()

    analyze_assertion(
        build_assertion("allow"),
        build_target("eni-0123456789abcdef0"),
        build_target("eni-0fedcba9876543210"),
        client,
        "222222222222",
        poll_interval_seconds=0.0,
    )

    assert client.deleted_analysis_ids == ["nia-1234567890abcdef0"]
    assert client.deleted_path_ids == ["nip-1234567890abcdef0"]


def test_analyze_assertion_attempts_cleanup_after_start_failure() -> None:
    client = FakeReachabilityAnalyzerClient(
        start_analysis_error=client_error("UnauthorizedOperation", "StartNetworkInsightsAnalysis")
    )

    with pytest.raises(ReachabilityAnalyzerError, match="Reachability Analyzer API error"):
        analyze_assertion(
            build_assertion("allow"),
            build_target("eni-0123456789abcdef0"),
            build_target("eni-0fedcba9876543210"),
            client,
            "222222222222",
            poll_interval_seconds=0.0,
        )

    assert client.deleted_analysis_ids == []
    assert client.deleted_path_ids == ["nip-1234567890abcdef0"]
