from __future__ import annotations

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber

from preflight.discovery import ResolvedTarget
from preflight.engines.reachability_analyzer import (
    ReachabilityAnalyzerError,
    analyze_assertion,
)
from preflight.models import Assertion, Endpoint, Selector
from tests.fakes import FakeReachabilityAnalyzerClient, boto3_client


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


def add_analysis_success_flow(
    stubber: Stubber,
    *,
    source: str,
    destination: str,
    port: int = 443,
    additional_accounts: list[str] | None = None,
    network_path_found: bool = True,
) -> None:
    stubber.add_response(
        "create_network_insights_path",
        {
            "NetworkInsightsPath": {
                "NetworkInsightsPathId": "nip-1234567890abcdef0",
                "NetworkInsightsPathArn": (
                    "arn:aws:ec2:us-east-1:222222222222:network-insights-path/nip-1234567890abcdef0"
                ),
            }
        },
        {
            "Source": source,
            "Destination": destination,
            "Protocol": "tcp",
            "DestinationPort": port,
        },
    )

    start_params: dict[str, object] = {
        "NetworkInsightsPathId": "nip-1234567890abcdef0",
    }
    if additional_accounts:
        start_params["AdditionalAccounts"] = additional_accounts

    stubber.add_response(
        "start_network_insights_analysis",
        {
            "NetworkInsightsAnalysis": {
                "NetworkInsightsAnalysisId": "nia-1234567890abcdef0",
            }
        },
        start_params,
    )
    stubber.add_response(
        "describe_network_insights_analyses",
        {
            "NetworkInsightsAnalyses": [
                {
                    "NetworkInsightsAnalysisId": "nia-1234567890abcdef0",
                    "NetworkInsightsAnalysisArn": (
                        "arn:aws:ec2:us-east-1:222222222222:"
                        "network-insights-analysis/nia-1234567890abcdef0"
                    ),
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


def test_analyze_assertion_reaches_same_account_targets_with_stubber() -> None:
    client = boto3_client("ec2")

    with Stubber(client) as stubber:
        add_analysis_success_flow(
            stubber,
            source="eni-0123456789abcdef0",
            destination="eni-0fedcba9876543210",
        )

        result = analyze_assertion(
            build_assertion("allow"),
            build_target("eni-0123456789abcdef0"),
            build_target("eni-0fedcba9876543210"),
            client,
            "222222222222",
            poll_interval_seconds=0.0,
        )
        stubber.assert_no_pending_responses()

    assert result.actual_outcome == "reachable"
    assert result.analysis_status == "succeeded"
    assert result.analysis_id == "nia-1234567890abcdef0"


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


def test_analyze_assertion_uses_arns_and_additional_accounts_with_stubber() -> None:
    client = boto3_client("ec2")

    with Stubber(client) as stubber:
        add_analysis_success_flow(
            stubber,
            source="eni-0123456789abcdef0",
            destination=(
                "arn:aws:ec2:us-east-1:111111111111:network-interface/eni-0fedcba9876543210"
            ),
            additional_accounts=["111111111111"],
        )

        result = analyze_assertion(
            build_assertion("allow"),
            build_target("eni-0123456789abcdef0", owner_id="222222222222"),
            build_target("eni-0fedcba9876543210", owner_id="111111111111"),
            client,
            "222222222222",
            poll_interval_seconds=0.0,
        )
        stubber.assert_no_pending_responses()

    assert result.actual_outcome == "reachable"
    assert result.cleanup.analysis_delete_succeeded is True
    assert result.cleanup.path_delete_succeeded is True


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


def test_analyze_assertion_surfaces_failed_analysis_status_with_stubber() -> None:
    client = boto3_client("ec2")

    with Stubber(client) as stubber:
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

        with pytest.raises(
            ReachabilityAnalyzerError,
            match="finished with status 'failed'",
        ) as excinfo:
            analyze_assertion(
                build_assertion("allow"),
                build_target("eni-0123456789abcdef0"),
                build_target("eni-0fedcba9876543210"),
                client,
                "222222222222",
                poll_interval_seconds=0.0,
            )

        stubber.assert_no_pending_responses()

    assert excinfo.value.analysis_status == "failed"
    assert excinfo.value.status_message == "analysis failed"
    assert excinfo.value.explanation_code == "UNKNOWN"
    assert excinfo.value.explanation_summary == "UNKNOWN: port 443"


def test_analyze_assertion_raises_when_path_creation_fails() -> None:
    client = FakeReachabilityAnalyzerClient(
        create_path_error=client_error("UnauthorizedOperation", "CreateNetworkInsightsPath")
    )

    with pytest.raises(ReachabilityAnalyzerError, match="Reachability Analyzer AWS SDK error"):
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

    with pytest.raises(ReachabilityAnalyzerError, match="Reachability Analyzer AWS SDK error"):
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


def test_analyze_assertion_uses_ids_for_same_account_targets() -> None:
    client = FakeReachabilityAnalyzerClient()

    analyze_assertion(
        build_assertion("allow"),
        build_target("eni-0123456789abcdef0", owner_id="222222222222"),
        build_target("eni-0fedcba9876543210", owner_id="222222222222"),
        client,
        "222222222222",
        poll_interval_seconds=0.0,
    )

    assert client.create_path_calls[0]["Source"] == "eni-0123456789abcdef0"
    assert client.create_path_calls[0]["Destination"] == "eni-0fedcba9876543210"
    assert "AdditionalAccounts" not in client.start_analysis_calls[0]


def test_analyze_assertion_uses_arn_and_additional_account_for_cross_account_destination() -> None:
    client = FakeReachabilityAnalyzerClient()

    analyze_assertion(
        build_assertion("allow"),
        build_target("eni-0123456789abcdef0", owner_id="222222222222"),
        build_target("eni-0fedcba9876543210", owner_id="111111111111"),
        client,
        "222222222222",
        poll_interval_seconds=0.0,
    )

    assert client.create_path_calls[0]["Source"] == "eni-0123456789abcdef0"
    assert client.create_path_calls[0]["Destination"] == (
        "arn:aws:ec2:us-east-1:111111111111:network-interface/eni-0fedcba9876543210"
    )
    assert client.start_analysis_calls[0]["AdditionalAccounts"] == ["111111111111"]


def test_analyze_assertion_dedupes_and_sorts_additional_accounts() -> None:
    client = FakeReachabilityAnalyzerClient()

    analyze_assertion(
        build_assertion("allow"),
        build_target("eni-0123456789abcdef0", owner_id="333333333333"),
        build_target("eni-0fedcba9876543210", owner_id="111111111111"),
        client,
        "222222222222",
        poll_interval_seconds=0.0,
    )

    assert client.create_path_calls[0]["Source"] == (
        "arn:aws:ec2:us-east-1:333333333333:network-interface/eni-0123456789abcdef0"
    )
    assert client.create_path_calls[0]["Destination"] == (
        "arn:aws:ec2:us-east-1:111111111111:network-interface/eni-0fedcba9876543210"
    )
    assert client.start_analysis_calls[0]["AdditionalAccounts"] == [
        "111111111111",
        "333333333333",
    ]


def test_analyze_assertion_dedupes_duplicate_additional_accounts() -> None:
    client = FakeReachabilityAnalyzerClient()

    analyze_assertion(
        build_assertion("allow"),
        build_target("eni-0123456789abcdef0", owner_id="111111111111"),
        build_target("eni-0fedcba9876543210", owner_id="111111111111"),
        client,
        "222222222222",
        poll_interval_seconds=0.0,
    )

    assert client.start_analysis_calls[0]["AdditionalAccounts"] == ["111111111111"]


def test_analyze_assertion_normalizes_botocore_runtime_errors() -> None:
    client = FakeReachabilityAnalyzerClient(
        describe_analysis_error=EndpointConnectionError(
            endpoint_url="https://ec2.us-east-1.amazonaws.com"
        )
    )

    with pytest.raises(ReachabilityAnalyzerError, match="Reachability Analyzer AWS SDK error"):
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


def test_analyze_assertion_records_cleanup_botocore_errors() -> None:
    client = FakeReachabilityAnalyzerClient(
        delete_path_error=EndpointConnectionError(
            endpoint_url="https://ec2.us-east-1.amazonaws.com"
        )
    )

    result = analyze_assertion(
        build_assertion("allow"),
        build_target("eni-0123456789abcdef0"),
        build_target("eni-0fedcba9876543210"),
        client,
        "222222222222",
        poll_interval_seconds=0.0,
    )

    assert result.cleanup.path_delete_attempted is True
    assert result.cleanup.path_delete_succeeded is False
    assert result.cleanup.errors
