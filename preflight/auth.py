"""AWS session helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import boto3
from boto3.session import Session

from preflight.models import AccountConfig, DefaultsConfig

ROLE_SESSION_NAME = "aws-network-preflight"


class SessionFactory:
    """Create boto3 sessions from config defaults and account settings."""

    def __init__(
        self,
        defaults: DefaultsConfig,
        accounts: Mapping[str, AccountConfig],
        profile_override: str | None = None,
    ) -> None:
        self._defaults = defaults
        self._accounts = accounts
        self._profile_override = profile_override

    def base_session(self, region: str | None = None) -> Session:
        """Build a base session using the default credential chain or a profile."""

        profile_name = self._profile_override

        if profile_name is None and self._defaults.auth.mode == "profile":
            profile_name = self._defaults.auth.profile

        session_kwargs: dict[str, Any] = {
            "region_name": region or self._defaults.region,
        }

        if profile_name is not None:
            session_kwargs["profile_name"] = profile_name

        return boto3.Session(**session_kwargs)

    def session_for_account(self, account_name: str, region: str | None = None) -> Session:
        """Build a session for a named account, assuming its role when configured."""

        account = self._accounts[account_name]
        session = self.base_session(region=region)

        if account.role_arn is None:
            return session

        sts = session.client("sts")
        response = sts.assume_role(RoleArn=account.role_arn, RoleSessionName=ROLE_SESSION_NAME)
        credentials = response["Credentials"]

        return boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=region or self._defaults.region,
        )
