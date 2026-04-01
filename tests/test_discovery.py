from __future__ import annotations

from typing import Any

import pytest

from preflight.discovery import (
    DiscoveredTarget,
    SelectorResolutionError,
    resolve_assertion_targets,
    resolve_target,
)
from preflight.models import Endpoint, PreflightConfig
from tests.fakes import FakeEC2Client, FakeSessionFactory, make_eni, make_instance


def build_config(selector: dict[str, Any]) -> tuple[PreflightConfig, Endpoint]:
    config = PreflightConfig.model_validate(
        {
            "version": 1,
            "defaults": {"region": "us-east-1"},
            "accounts": {"app": {"regions": ["us-east-1"]}},
            "assertions": [
                {
                    "id": "assertion-1",
                    "type": "allow",
                    "source": {"account": "app", "selector": selector},
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
    return config, config.assertions[0].source


def test_resolve_unique_eni_by_resource_id() -> None:
    config, endpoint = build_config({"resource_id": "eni-0123456789abcdef0"})
    ec2_client = FakeEC2Client(
        enis_by_id={"eni-0123456789abcdef0": make_eni("eni-0123456789abcdef0")}
    )

    resolved = resolve_target(config, endpoint, ec2_client=ec2_client)

    assert resolved.input_type == "eni"
    assert resolved.resolved_target_type == "eni"
    assert resolved.resolved_identifier == "eni-0123456789abcdef0"
    assert resolved.normalized is False


def test_resolve_unique_ec2_instance_by_resource_id() -> None:
    config, endpoint = build_config({"resource_id": "i-0123456789abcdef0"})
    ec2_client = FakeEC2Client(
        instances_by_id={
            "i-0123456789abcdef0": make_instance(
                "i-0123456789abcdef0",
                primary_eni_id="eni-0123456789abcdef0",
            )
        }
    )

    resolved = resolve_target(config, endpoint, ec2_client=ec2_client)

    assert resolved.input_type == "ec2_instance"
    assert resolved.input_identifier == "i-0123456789abcdef0"
    assert resolved.resolved_target_type == "eni"
    assert resolved.resolved_identifier == "eni-0123456789abcdef0"
    assert resolved.normalized is True


def test_resolve_unique_eni_by_arn() -> None:
    config, endpoint = build_config(
        {"arn": "arn:aws:ec2:us-east-1:222222222222:network-interface/eni-0123456789abcdef0"}
    )
    ec2_client = FakeEC2Client(
        enis_by_id={"eni-0123456789abcdef0": make_eni("eni-0123456789abcdef0")}
    )

    resolved = resolve_target(config, endpoint, ec2_client=ec2_client)

    assert resolved.input_type == "eni"
    assert resolved.input_arn.endswith("network-interface/eni-0123456789abcdef0")
    assert resolved.resolved_identifier == "eni-0123456789abcdef0"


def test_resolve_unique_ec2_instance_by_arn() -> None:
    config, endpoint = build_config(
        {"arn": "arn:aws:ec2:us-east-1:222222222222:instance/i-0123456789abcdef0"}
    )
    ec2_client = FakeEC2Client(
        instances_by_id={
            "i-0123456789abcdef0": make_instance(
                "i-0123456789abcdef0",
                primary_eni_id="eni-0fedcba9876543210",
            )
        }
    )

    resolved = resolve_target(config, endpoint, ec2_client=ec2_client)

    assert resolved.input_type == "ec2_instance"
    assert resolved.resolved_identifier == "eni-0fedcba9876543210"
    assert resolved.normalized is True


def test_resolve_unique_target_by_tags() -> None:
    config, endpoint = build_config({"tags": {"Name": "app-dev"}})
    ec2_client = FakeEC2Client(tag_enis=[make_eni("eni-0123456789abcdef0")])

    resolved = resolve_target(config, endpoint, ec2_client=ec2_client)

    assert resolved.input_type == "eni"
    assert resolved.resolved_identifier == "eni-0123456789abcdef0"


def test_resolve_target_fails_when_zero_matches_are_found() -> None:
    config, endpoint = build_config({"tags": {"Name": "missing"}})
    ec2_client = FakeEC2Client()

    with pytest.raises(SelectorResolutionError, match="No supported v1 targets matched"):
        resolve_target(config, endpoint, ec2_client=ec2_client)


def test_resolve_target_fails_when_multiple_matches_are_found() -> None:
    config, endpoint = build_config({"tags": {"Name": "ambiguous"}})
    ec2_client = FakeEC2Client(
        tag_enis=[make_eni("eni-0123456789abcdef0")],
        tag_instances=[
            make_instance(
                "i-0123456789abcdef0",
                primary_eni_id="eni-0fedcba9876543210",
            )
        ],
    )

    with pytest.raises(SelectorResolutionError, match="matched multiple supported v1 targets"):
        resolve_target(config, endpoint, ec2_client=ec2_client)


def test_resolve_target_fails_for_unsupported_resource_id_pattern() -> None:
    config, endpoint = build_config({"resource_id": "sg-0123456789abcdef0"})

    with pytest.raises(SelectorResolutionError, match="Unsupported resource_id pattern"):
        resolve_target(config, endpoint, ec2_client=FakeEC2Client())


def test_resolve_target_fails_for_unsupported_arn_resource_type() -> None:
    config, endpoint = build_config(
        {"arn": "arn:aws:ec2:us-east-1:222222222222:subnet/subnet-0123456789abcdef0"}
    )

    with pytest.raises(SelectorResolutionError, match="Unsupported ARN resource type"):
        resolve_target(config, endpoint, ec2_client=FakeEC2Client())


def test_resolve_target_fails_for_unsupported_tag_match_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, endpoint = build_config({"tags": {"Name": "unsupported"}})

    monkeypatch.setattr(
        "preflight.discovery._discover_enis_by_tags",
        lambda **kwargs: [
            DiscoveredTarget(
                account="app",
                region="us-east-1",
                target_type="subnet",
                resource_id="subnet-0123456789abcdef0",
                arn="arn:aws:ec2:us-east-1:222222222222:subnet/subnet-0123456789abcdef0",
            )
        ],
    )
    monkeypatch.setattr("preflight.discovery._discover_instances_by_tags", lambda **kwargs: [])

    with pytest.raises(SelectorResolutionError, match="Matched unsupported v1 target type"):
        resolve_target(config, endpoint, ec2_client=FakeEC2Client())


def test_resolve_assertion_targets_returns_source_and_destination_targets() -> None:
    config, _ = build_config({"resource_id": "eni-0123456789abcdef0"})
    session_factory = FakeSessionFactory(
        {
            "app": FakeEC2Client(
                enis_by_id={
                    "eni-0123456789abcdef0": make_eni("eni-0123456789abcdef0"),
                    "eni-0fedcba9876543210": make_eni("eni-0fedcba9876543210"),
                }
            )
        }
    )

    resolved_targets = resolve_assertion_targets(config, session_factory=session_factory)

    assert len(resolved_targets) == 2
    assert resolved_targets[0].endpoint_role == "source"
    assert resolved_targets[1].endpoint_role == "destination"
