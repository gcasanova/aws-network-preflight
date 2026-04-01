from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from preflight.cli import app
from preflight.config import STARTER_CONFIG_YAML
from preflight.discovery import ResolvedTarget
from preflight.engines.reachability_analyzer import CleanupSummary
from preflight.exit_codes import ExitCode
from preflight.models import Selector
from preflight.runner import AssertionResult, RunSummary

runner = CliRunner()


def build_target(
    eni_id: str,
    *,
    normalized: bool = False,
    input_type: str = "eni",
    input_identifier: str | None = None,
) -> ResolvedTarget:
    return ResolvedTarget(
        account="app",
        region="us-east-1",
        selector=Selector(resource_id=input_identifier or eni_id),
        selector_type="resource_id",
        input_type=input_type,  # type: ignore[arg-type]
        input_identifier=input_identifier or eni_id,
        input_arn=f"arn:aws:ec2:us-east-1:222222222222:network-interface/{eni_id}",
        resolved_target_type="eni",
        resolved_identifier=eni_id,
        resolved_arn=f"arn:aws:ec2:us-east-1:222222222222:network-interface/{eni_id}",
        normalized=normalized,
        metadata={"owner_id": "222222222222"},
    )


def build_result(
    *,
    status: str,
    expected_outcome: str = "reachable",
    actual_outcome: str = "reachable",
    message: str = "analysis complete",
    normalized_source: bool = False,
) -> AssertionResult:
    return AssertionResult(
        assertion_id="assertion-1",
        assertion_type="allow",
        expected_outcome=expected_outcome,  # type: ignore[arg-type]
        actual_outcome=actual_outcome,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        message=message,
        source=build_target(
            "eni-0123456789abcdef0",
            normalized=normalized_source,
            input_type="ec2_instance" if normalized_source else "eni",
            input_identifier="i-0123456789abcdef0" if normalized_source else None,
        ),
        destination=build_target("eni-0fedcba9876543210"),
        path_id="nip-1234567890abcdef0",
        analysis_id="nia-1234567890abcdef0",
        analysis_status="succeeded",
        network_path_found=actual_outcome == "reachable",
        explanation_code="SECURITY_GROUP_RULE_MISMATCH" if status != "passed" else None,
        explanation_summary="SECURITY_GROUP_RULE_MISMATCH: SecurityGroup sg-1234"
        if status != "passed"
        else None,
        cleanup=CleanupSummary(),
    )


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "preflight.yaml"
    config_path.write_text(STARTER_CONFIG_YAML, encoding="utf-8")
    return config_path


def test_run_command_exits_zero_when_all_assertions_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        "preflight.cli.run_assertions",
        lambda *args, **kwargs: RunSummary(results=[build_result(status="passed")]),
    )

    result = runner.invoke(app, ["run", "-f", str(config_path)])

    assert result.exit_code == int(ExitCode.OK)
    assert "Assertion Results" in result.stdout
    assert "passed" in result.stdout


def test_run_command_exits_one_when_any_assertion_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        "preflight.cli.run_assertions",
        lambda *args, **kwargs: RunSummary(
            results=[
                build_result(status="passed"),
                build_result(
                    status="failed",
                    actual_outcome="not_reachable",
                    message="Expected reachable but Reachability Analyzer reported not reachable.",
                ),
            ]
        ),
    )

    result = runner.invoke(app, ["run", "-f", str(config_path)])

    assert result.exit_code == int(ExitCode.ASSERTION_FAILED)
    assert "Failed: 1" in result.stdout


def test_run_command_exits_three_on_runtime_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        "preflight.cli.run_assertions",
        lambda *args, **kwargs: RunSummary(
            results=[
                build_result(
                    status="error",
                    actual_outcome="error",
                    message="Reachability Analyzer API error: boom",
                )
            ]
        ),
    )

    result = runner.invoke(app, ["run", "-f", str(config_path)])

    assert result.exit_code == int(ExitCode.RUNTIME_ERROR)
    assert "Errors: 1" in result.stdout


def test_run_command_supports_json_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        "preflight.cli.run_assertions",
        lambda *args, **kwargs: RunSummary(
            results=[
                build_result(status="passed"),
                build_result(
                    status="failed",
                    actual_outcome="not_reachable",
                    message="Expected reachable but Reachability Analyzer reported not reachable.",
                ),
            ]
        ),
    )

    result = runner.invoke(app, ["run", "-f", str(config_path), "--format", "json"])

    assert result.exit_code == int(ExitCode.ASSERTION_FAILED)
    payload = json.loads(result.stdout)
    assert payload["passed_count"] == 1
    assert payload["failed_count"] == 1
    assert payload["results"][0]["assertion_id"] == "assertion-1"


def test_explain_command_prints_successful_assertion_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        "preflight.cli.run_assertion",
        lambda *args, **kwargs: build_result(
            status="passed",
            message="Expected reachable and Reachability Analyzer reported reachable.",
            normalized_source=True,
        ),
    )

    result = runner.invoke(app, ["explain", "-f", str(config_path), "--id", "assertion-1"])

    assert result.exit_code == int(ExitCode.OK)
    assert "Assertion assertion-1" in result.stdout
    assert "ec2_instance i-0123456789abcdef0 -> eni eni-0123456789abcdef0" in result.stdout
    assert "Network Path Found" in result.stdout


def test_explain_command_prints_failed_assertion_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        "preflight.cli.run_assertion",
        lambda *args, **kwargs: build_result(
            status="failed",
            actual_outcome="not_reachable",
            message="Expected reachable but Reachability Analyzer reported not reachable.",
        ),
    )

    result = runner.invoke(app, ["explain", "-f", str(config_path), "--id", "assertion-1"])

    assert result.exit_code == int(ExitCode.ASSERTION_FAILED)
    assert "SECURITY_GROUP_RULE_MISMATCH" in result.stdout
    assert "Expected reachable but Reachability Analyzer reported not reachable." in result.stdout


def test_explain_command_supports_json_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        "preflight.cli.run_assertion",
        lambda *args, **kwargs: build_result(
            status="failed",
            actual_outcome="not_reachable",
            message="Expected reachable but Reachability Analyzer reported not reachable.",
            normalized_source=True,
        ),
    )

    result = runner.invoke(
        app,
        ["explain", "-f", str(config_path), "--id", "assertion-1", "--format", "json"],
    )

    assert result.exit_code == int(ExitCode.ASSERTION_FAILED)
    payload = json.loads(result.stdout)
    assert payload["assertion_id"] == "assertion-1"
    assert payload["status"] == "failed"
    assert payload["source"]["normalized"] is True
    assert payload["message"] == (
        "Expected reachable but Reachability Analyzer reported not reachable."
    )
