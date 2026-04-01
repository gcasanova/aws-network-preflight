from __future__ import annotations

import json

from preflight.discovery import ResolvedTarget
from preflight.engines.reachability_analyzer import CleanupSummary
from preflight.models import Selector
from preflight.reporters.json_report import render_json
from preflight.runner import AssertionResult, RunSummary


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


def build_assertion_result() -> AssertionResult:
    return AssertionResult(
        assertion_id="assertion-1",
        assertion_type="allow",
        expected_outcome="reachable",
        actual_outcome="reachable",
        status="passed",
        message="Expected reachable and Reachability Analyzer reported reachable.",
        source=build_target("eni-0123456789abcdef0"),
        destination=build_target("eni-0fedcba9876543210"),
        path_id="nip-1234567890abcdef0",
        analysis_id="nia-1234567890abcdef0",
        analysis_status="succeeded",
        network_path_found=True,
        cleanup=CleanupSummary(
            analysis_delete_attempted=True,
            analysis_delete_succeeded=True,
            path_delete_attempted=True,
            path_delete_succeeded=True,
        ),
    )


def test_render_json_serializes_real_assertion_result() -> None:
    payload = json.loads(render_json(build_assertion_result()))

    assert payload["assertion_id"] == "assertion-1"
    assert payload["source"]["selector"] == {"resource_id": "eni-0123456789abcdef0"}
    assert payload["source"]["metadata"] == {"owner_id": "222222222222"}
    assert payload["cleanup"]["analysis_delete_succeeded"] is True


def test_render_json_serializes_run_summary_with_counts() -> None:
    result = build_assertion_result()
    summary = RunSummary(results=[result])

    payload = json.loads(render_json(summary))

    assert payload["passed_count"] == 1
    assert payload["failed_count"] == 0
    assert payload["error_count"] == 0
    assert payload["results"][0]["destination"]["selector"]["resource_id"] == (
        "eni-0fedcba9876543210"
    )
