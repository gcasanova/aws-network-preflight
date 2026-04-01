from __future__ import annotations

from typing import Any

import pytest

from preflight.auth import AccountIdentityError, SessionFactory
from preflight.discovery import (
    DiscoveredTarget,
    SelectorResolutionError,
    resolve_assertion_targets,
    resolve_target,
)
from preflight.models import Endpoint, PreflightConfig
from tests.fakes import (
    FakeEC2Client,
    FakeSession,
    FakeSessionFactory,
    FakeSTSClient,
    make_eni,
    make_instance,
)


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
    assert (
        resolved.resolved_arn
        == "arn:aws:ec2:us-east-1:222222222222:network-interface/eni-0123456789abcdef0"
    )


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
    assert resolved.input_arn == "arn:aws:ec2:us-east-1:222222222222:instance/i-0123456789abcdef0"
    assert (
        resolved.resolved_arn
        == "arn:aws:ec2:us-east-1:222222222222:network-interface/eni-0fedcba9876543210"
    )


def test_resolve_unique_target_by_tags() -> None:
    config, endpoint = build_config({"tags": {"Name": "app-dev"}})
    ec2_client = FakeEC2Client(tag_enis=[make_eni("eni-0123456789abcdef0")])

    resolved = resolve_target(config, endpoint, ec2_client=ec2_client)

    assert resolved.input_type == "eni"
    assert resolved.resolved_identifier == "eni-0123456789abcdef0"


def test_resolve_tagged_enis_across_all_pages() -> None:
    config, endpoint = build_config({"tags": {"Name": "paged-eni"}})
    ec2_client = FakeEC2Client(
        tag_eni_pages=[
            [],
            [make_eni("eni-0123456789abcdef0")],
        ]
    )

    resolved = resolve_target(config, endpoint, ec2_client=ec2_client)

    assert resolved.resolved_identifier == "eni-0123456789abcdef0"


def test_resolve_tagged_instances_across_all_pages() -> None:
    config, endpoint = build_config({"tags": {"Name": "paged-instance"}})
    ec2_client = FakeEC2Client(
        tag_instance_pages=[
            [],
            [make_instance("i-0123456789abcdef0", primary_eni_id="eni-0123456789abcdef0")],
        ]
    )

    resolved = resolve_target(config, endpoint, ec2_client=ec2_client)

    assert resolved.input_type == "ec2_instance"
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


def test_resolve_target_fails_when_instance_and_eni_share_same_tag_before_normalization() -> None:
    config, endpoint = build_config({"tags": {"Name": "same-target"}})
    ec2_client = FakeEC2Client(
        tag_enis=[make_eni("eni-0123456789abcdef0")],
        tag_instances=[
            make_instance(
                "i-0123456789abcdef0",
                primary_eni_id="eni-0123456789abcdef0",
            )
        ],
    )

    with pytest.raises(
        SelectorResolutionError,
        match="enforces tag uniqueness before normalization",
    ):
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


def test_resolve_target_fails_for_arn_region_mismatch() -> None:
    config, endpoint = build_config(
        {"arn": "arn:aws:ec2:us-west-2:222222222222:network-interface/eni-0123456789abcdef0"}
    )

    with pytest.raises(SelectorResolutionError, match="effective region us-east-1"):
        resolve_target(config, endpoint, ec2_client=FakeEC2Client())


def test_resolve_target_fails_for_arn_account_mismatch() -> None:
    config, endpoint = build_config(
        {"arn": "arn:aws:ec2:us-east-1:111111111111:network-interface/eni-0123456789abcdef0"}
    )
    ec2_client = FakeEC2Client(
        account_id="222222222222",
        enis_by_id={"eni-0123456789abcdef0": make_eni("eni-0123456789abcdef0")},
    )

    with pytest.raises(SelectorResolutionError, match="resolves in account 222222222222"):
        resolve_target(config, endpoint, ec2_client=ec2_client)


def test_resolve_assertion_targets_uses_effective_account_identity_for_matching_arn() -> None:
    config, _ = build_config(
        {"arn": "arn:aws:ec2:us-east-1:222222222222:network-interface/eni-0123456789abcdef0"}
    )
    session_factory = FakeSessionFactory(
        {
            "app": FakeEC2Client(
                account_id="222222222222",
                enis_by_id={
                    "eni-0123456789abcdef0": make_eni("eni-0123456789abcdef0"),
                    "eni-0fedcba9876543210": make_eni("eni-0fedcba9876543210"),
                },
            )
        }
    )

    resolved_targets = resolve_assertion_targets(config, session_factory=session_factory)

    assert len(resolved_targets) == 2
    assert resolved_targets[0].target.input_arn.endswith("eni-0123456789abcdef0")


def test_resolve_assertion_targets_fails_for_real_account_id_mismatch() -> None:
    config, _ = build_config(
        {"arn": "arn:aws:ec2:us-east-1:111111111111:network-interface/eni-0123456789abcdef0"}
    )
    session_factory = FakeSessionFactory(
        {
            "app": FakeEC2Client(
                account_id="222222222222",
                enis_by_id={
                    "eni-0123456789abcdef0": make_eni("eni-0123456789abcdef0"),
                    "eni-0fedcba9876543210": make_eni("eni-0fedcba9876543210"),
                },
            )
        }
    )

    with pytest.raises(SelectorResolutionError, match="resolves in account 222222222222"):
        resolve_assertion_targets(config, session_factory=session_factory)


def test_resolve_assertion_targets_fails_when_account_identity_lookup_fails() -> None:
    config, _ = build_config(
        {"arn": "arn:aws:ec2:us-east-1:222222222222:network-interface/eni-0123456789abcdef0"}
    )
    session_factory = FakeSessionFactory(
        {
            "app": FakeEC2Client(
                account_id="222222222222",
                enis_by_id={
                    "eni-0123456789abcdef0": make_eni("eni-0123456789abcdef0"),
                    "eni-0fedcba9876543210": make_eni("eni-0fedcba9876543210"),
                },
            )
        },
        sts_clients_by_account={
            "app": FakeSTSClient(raise_error=AccountIdentityError("identity lookup failed"))
        },
    )

    with pytest.raises(AccountIdentityError, match="identity lookup failed"):
        resolve_assertion_targets(config, session_factory=session_factory)


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


def test_resolve_target_fails_for_unsupported_partition() -> None:
    config, endpoint = build_config(
        {"arn": "arn:aws-us-gov:ec2:us-east-1:222222222222:instance/i-0123456789abcdef0"}
    )

    with pytest.raises(
        SelectorResolutionError,
        match="only supports the standard commercial AWS partition",
    ):
        resolve_target(config, endpoint, ec2_client=FakeEC2Client())


def test_resolve_target_fails_for_no_primary_eni() -> None:
    config, endpoint = build_config({"resource_id": "i-0123456789abcdef0"})
    ec2_client = FakeEC2Client(
        instances_by_id={
            "i-0123456789abcdef0": {
                "OwnerId": "222222222222",
                "Instances": [{"InstanceId": "i-0123456789abcdef0", "NetworkInterfaces": []}],
            }
        }
    )

    with pytest.raises(SelectorResolutionError, match="did not include any network interfaces"):
        resolve_target(config, endpoint, ec2_client=ec2_client)


def test_resolve_target_fails_for_multiple_primary_enis() -> None:
    config, endpoint = build_config({"resource_id": "i-0123456789abcdef0"})
    ec2_client = FakeEC2Client(
        instances_by_id={
            "i-0123456789abcdef0": {
                "OwnerId": "222222222222",
                "Instances": [
                    {
                        "InstanceId": "i-0123456789abcdef0",
                        "NetworkInterfaces": [
                            {"NetworkInterfaceId": "eni-1", "Attachment": {"DeviceIndex": 0}},
                            {"NetworkInterfaceId": "eni-2", "Attachment": {"DeviceIndex": 0}},
                        ],
                    }
                ],
            }
        }
    )

    with pytest.raises(SelectorResolutionError, match="exactly one primary ENI"):
        resolve_target(config, endpoint, ec2_client=ec2_client)


def test_resolve_target_fails_for_malformed_network_interface_shape() -> None:
    config, endpoint = build_config({"resource_id": "i-0123456789abcdef0"})
    ec2_client = FakeEC2Client(
        instances_by_id={
            "i-0123456789abcdef0": {
                "OwnerId": "222222222222",
                "Instances": [{"InstanceId": "i-0123456789abcdef0", "NetworkInterfaces": "bad"}],
            }
        }
    )

    with pytest.raises(SelectorResolutionError, match="invalid NetworkInterfaces shape"):
        resolve_target(config, endpoint, ec2_client=ec2_client)


def test_resolve_target_fails_for_invalid_instance_shape_inside_reservation() -> None:
    config, endpoint = build_config({"tags": {"Name": "bad-shape"}})
    ec2_client = FakeEC2Client(tag_instance_pages=[["not-a-dict"]])

    with pytest.raises(SelectorResolutionError, match="invalid response shape"):
        resolve_target(config, endpoint, ec2_client=ec2_client)


def test_resolve_target_fails_for_too_short_resource_id() -> None:
    config, endpoint = build_config({"resource_id": "i-1234"})

    with pytest.raises(SelectorResolutionError, match="Unsupported resource_id pattern"):
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


def test_session_factory_caches_effective_account_identity() -> None:
    config, _ = build_config({"resource_id": "eni-0123456789abcdef0"})
    session_factory = SessionFactory(config.defaults, config.accounts)
    fake_sts = FakeSTSClient(account_id="222222222222")
    fake_session = FakeSession(FakeEC2Client(account_id="222222222222"), fake_sts)

    def fake_session_for_account(account_name: str, region: str | None = None) -> FakeSession:
        _ = account_name, region
        return fake_session

    session_factory.session_for_account = fake_session_for_account  # type: ignore[method-assign]

    assert session_factory.account_id_for_account("app") == "222222222222"
    assert session_factory.account_id_for_account("app") == "222222222222"
    assert fake_sts.get_caller_identity_calls == 1
