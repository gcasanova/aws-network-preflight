"""AWS session helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import boto3
from boto3.session import Session

from preflight.models import AccountConfig, DefaultsConfig

ROLE_SESSION_NAME = "aws-network-preflight"


class AccountIdentityError(RuntimeError):
    """Raised when the effective AWS account ID cannot be determined."""


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
        self._session_cache: dict[tuple[str, str], Session] = {}
        self._account_id_cache: dict[tuple[str, str], str] = {}

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

        effective_region = region or self._defaults.region
        cache_key = (account_name, effective_region)
        cached_session = self._session_cache.get(cache_key)
        if cached_session is not None:
            return cached_session

        account = self._accounts[account_name]
        session = self.base_session(region=effective_region)

        if account.role_arn is None:
            self._session_cache[cache_key] = session
            return session

        sts = session.client("sts")
        response = sts.assume_role(RoleArn=account.role_arn, RoleSessionName=ROLE_SESSION_NAME)
        credentials = response["Credentials"]

        assumed_session = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=effective_region,
        )
        self._session_cache[cache_key] = assumed_session
        return assumed_session

    def account_id_for_account(self, account_name: str, region: str | None = None) -> str:
        """Return the effective AWS account ID for a configured account session."""

        effective_region = region or self._defaults.region
        cache_key = (account_name, effective_region)
        cached_account_id = self._account_id_cache.get(cache_key)
        if cached_account_id is not None:
            return cached_account_id

        session = self.session_for_account(account_name, region=effective_region)
        sts = session.client("sts")
        response = sts.get_caller_identity()
        account_id = response.get("Account")

        if not isinstance(account_id, str) or not account_id:
            raise AccountIdentityError(
                f"Failed to determine effective AWS account ID for account '{account_name}'."
            )

        self._account_id_cache[cache_key] = account_id
        return account_id
