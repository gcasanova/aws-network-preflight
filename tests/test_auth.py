from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from preflight.auth import ROLE_SESSION_NAME, AccountIdentityError, SessionFactory
from preflight.models import PreflightConfig
from tests.fakes import boto3_client


class StubSession:
    def __init__(self, sts_client: Any) -> None:
        self._sts_client = sts_client

    def client(self, service_name: str) -> Any:
        if service_name != "sts":
            raise AssertionError(f"Unexpected service request: {service_name}")
        return self._sts_client


def build_config(*, role_arn: str | None = None) -> PreflightConfig:
    return PreflightConfig.model_validate(
        {
            "version": 1,
            "defaults": {"region": "us-east-1"},
            "accounts": {
                "app": {
                    "regions": ["us-east-1"],
                    "role_arn": role_arn,
                }
            },
            "assertions": [
                {
                    "id": "assertion-1",
                    "type": "allow",
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


def test_session_for_account_assumes_role_and_caches_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role_arn = "arn:aws:iam::111111111111:role/PreflightReadRole"
    config = build_config(role_arn=role_arn)
    factory = SessionFactory(config.defaults, config.accounts)
    sts_client = boto3_client("sts")
    assumed_session = object()
    captured_kwargs: list[dict[str, Any]] = []

    with Stubber(sts_client) as stubber:
        stubber.add_response(
            "assume_role",
            {
                "Credentials": {
                    "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
                    "SecretAccessKey": "secret",
                    "SessionToken": "token",
                    "Expiration": datetime(2030, 1, 1, tzinfo=UTC),
                },
                "AssumedRoleUser": {
                    "AssumedRoleId": "ARO123EXAMPLE:aws-network-preflight",
                    "Arn": (
                        "arn:aws:sts::111111111111:assumed-role/"
                        "PreflightReadRole/aws-network-preflight"
                    ),
                },
            },
            {"RoleArn": role_arn, "RoleSessionName": ROLE_SESSION_NAME},
        )

        monkeypatch.setattr(factory, "base_session", lambda region=None: StubSession(sts_client))

        def fake_boto3_session(**kwargs: Any) -> object:
            captured_kwargs.append(kwargs)
            return assumed_session

        monkeypatch.setattr("preflight.auth.boto3.Session", fake_boto3_session)

        first = factory.session_for_account("app", region="us-east-1")
        second = factory.session_for_account("app", region="us-east-1")
        stubber.assert_no_pending_responses()

    assert first is assumed_session
    assert second is assumed_session
    assert captured_kwargs == [
        {
            "aws_access_key_id": "ASIAIOSFODNN7EXAMPLE",
            "aws_secret_access_key": "secret",
            "aws_session_token": "token",
            "region_name": "us-east-1",
        }
    ]


def test_session_for_account_propagates_assume_role_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role_arn = "arn:aws:iam::111111111111:role/PreflightReadRole"
    config = build_config(role_arn=role_arn)
    factory = SessionFactory(config.defaults, config.accounts)
    sts_client = boto3_client("sts")

    with Stubber(sts_client) as stubber:
        stubber.add_client_error(
            "assume_role",
            service_error_code="AccessDenied",
            service_message="denied",
            expected_params={"RoleArn": role_arn, "RoleSessionName": ROLE_SESSION_NAME},
        )
        monkeypatch.setattr(factory, "base_session", lambda region=None: StubSession(sts_client))

        with pytest.raises(ClientError) as excinfo:
            factory.session_for_account("app", region="us-east-1")

        stubber.assert_no_pending_responses()

    assert excinfo.value.response["Error"]["Code"] == "AccessDenied"


def test_account_id_for_account_raises_when_sts_identity_lacks_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_config()
    factory = SessionFactory(config.defaults, config.accounts)
    sts_client = boto3_client("sts")

    with Stubber(sts_client) as stubber:
        stubber.add_response(
            "get_caller_identity",
            {
                "UserId": "ARO123EXAMPLE:aws-network-preflight",
                "Arn": "arn:aws:sts::222222222222:assumed-role/PreflightReadRole/session",
            },
            {},
        )
        monkeypatch.setattr(
            factory,
            "session_for_account",
            lambda account_name, region=None: StubSession(sts_client),
        )

        with pytest.raises(
            AccountIdentityError,
            match="Failed to determine effective AWS account ID",
        ):
            factory.account_id_for_account("app", region="us-east-1")

        stubber.assert_no_pending_responses()
