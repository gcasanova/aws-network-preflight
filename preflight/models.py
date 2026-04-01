"""Pydantic models for the YAML configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and trims strings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AuthConfig(StrictModel):
    """Authentication defaults used to build AWS sessions."""

    mode: Literal["default", "profile"] = "default"
    profile: str | None = None

    @model_validator(mode="after")
    def validate_profile_requirements(self) -> AuthConfig:
        if self.mode == "profile" and not self.profile:
            raise ValueError("defaults.auth.profile is required when mode='profile'")
        return self


class DefaultsConfig(StrictModel):
    """Global defaults for region and auth."""

    region: str
    auth: AuthConfig = Field(default_factory=AuthConfig)


class AccountConfig(StrictModel):
    """Per-account connection details."""

    role_arn: str | None = None
    regions: list[str] = Field(min_length=1)


class Selector(StrictModel):
    """Selector that must identify exactly one resource."""

    resource_id: str | None = None
    arn: str | None = None
    tags: dict[str, str] | None = None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return value

        if not value:
            raise ValueError("selector.tags must not be empty")

        for key, item_value in value.items():
            if not key or not item_value:
                raise ValueError("selector.tags entries must use non-empty keys and values")

        return value

    @model_validator(mode="after")
    def validate_exactly_one_selector(self) -> Selector:
        selected_fields = [
            field_name
            for field_name, field_value in (
                ("resource_id", self.resource_id),
                ("arn", self.arn),
                ("tags", self.tags),
            )
            if field_value is not None
        ]

        if len(selected_fields) != 1:
            raise ValueError("selector must define exactly one of resource_id, arn, or tags")

        return self


class Endpoint(StrictModel):
    """Reference to one side of an assertion."""

    account: str
    selector: Selector


class Assertion(StrictModel):
    """Connectivity intent to validate."""

    id: str
    type: Literal["allow", "deny"]
    source: Endpoint
    destination: Endpoint
    protocol: Literal["tcp", "udp"]
    port: int = Field(ge=1, le=65535)


class PreflightConfig(StrictModel):
    """Root configuration file."""

    version: int
    defaults: DefaultsConfig
    accounts: dict[str, AccountConfig]
    assertions: list[Assertion] = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("only config version 1 is supported")
        return value

    @field_validator("accounts")
    @classmethod
    def validate_accounts_not_empty(
        cls,
        value: dict[str, AccountConfig],
    ) -> dict[str, AccountConfig]:
        if not value:
            raise ValueError("at least one account must be defined")
        return value

    @field_validator("assertions")
    @classmethod
    def validate_unique_ids(cls, value: list[Assertion]) -> list[Assertion]:
        seen: set[str] = set()

        for assertion in value:
            if assertion.id in seen:
                raise ValueError(f"assertion ids must be unique; duplicate id '{assertion.id}'")
            seen.add(assertion.id)

        return value

    @field_validator("assertions")
    @classmethod
    def validate_account_references(
        cls,
        value: list[Assertion],
        info: ValidationInfo,
    ) -> list[Assertion]:
        accounts = info.data.get("accounts", {})

        for index, assertion in enumerate(value):
            if assertion.source.account not in accounts:
                raise ValueError(
                    f"assertions[{index}].source.account references unknown account "
                    f"'{assertion.source.account}'"
                )

            if assertion.destination.account not in accounts:
                raise ValueError(
                    f"assertions[{index}].destination.account references unknown account "
                    f"'{assertion.destination.account}'"
                )

        return value
