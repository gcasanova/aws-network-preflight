"""Target resolution primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from botocore.exceptions import ClientError

from preflight.auth import SessionFactory
from preflight.models import Endpoint, PreflightConfig, Selector

TargetType = Literal["eni", "ec2_instance"]
SelectorType = Literal["resource_id", "arn", "tags"]
EndpointRole = Literal["source", "destination"]
AWS_PARTITION = "aws"

INSTANCE_ID_PATTERN = re.compile(r"^i-(?:[0-9a-f]{8}|[0-9a-f]{17})$")
ENI_ID_PATTERN = re.compile(r"^eni-(?:[0-9a-f]{8}|[0-9a-f]{17})$")
EC2_ARN_PATTERN = re.compile(
    r"^arn:(?P<partition>[^:]+):ec2:(?P<region>[^:]+):(?P<account_id>\d{12}):"
    r"(?P<resource_type>[^/]+)/(?P<resource_id>.+)$"
)


@dataclass(slots=True)
class DiscoveredTarget:
    """A concrete supported target discovered from AWS APIs."""

    account: str
    region: str
    target_type: str
    resource_id: str
    arn: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedTarget:
    """A uniquely resolved AWS target ready for future execution."""

    account: str
    region: str
    selector: Selector
    selector_type: SelectorType
    input_type: TargetType
    input_identifier: str
    input_arn: str
    resolved_target_type: Literal["eni"]
    resolved_identifier: str
    resolved_arn: str
    normalized: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedAssertionTarget:
    """One resolved assertion endpoint."""

    assertion_id: str
    endpoint_role: EndpointRole
    target: ResolvedTarget


class SelectorResolutionError(RuntimeError):
    """Raised when a selector cannot be resolved unambiguously."""


def resolve_assertion_targets(
    config: PreflightConfig,
    session_factory: SessionFactory | None = None,
) -> list[ResolvedAssertionTarget]:
    """Resolve all assertion endpoints in a config."""

    effective_region = config.defaults.region
    resolved_targets: list[ResolvedAssertionTarget] = []
    client_cache: dict[str, Any] = {}
    account_id_cache: dict[str, str] = {}

    if session_factory is None:
        session_factory = SessionFactory(config.defaults, config.accounts)

    for assertion in config.assertions:
        endpoint_pairs: tuple[tuple[EndpointRole, Endpoint], ...] = (
            ("source", assertion.source),
            ("destination", assertion.destination),
        )
        for endpoint_role, endpoint in endpoint_pairs:
            account = endpoint.account

            if account not in client_cache:
                session = session_factory.session_for_account(account, region=effective_region)
                client_cache[account] = session.client("ec2")

            effective_account_id: str | None = None
            if endpoint.selector.arn is not None:
                if account not in account_id_cache:
                    account_id_cache[account] = session_factory.account_id_for_account(
                        account,
                        region=effective_region,
                    )
                effective_account_id = account_id_cache[account]

            target = resolve_target(
                config,
                endpoint,
                ec2_client=client_cache[account],
                effective_account_id=effective_account_id,
            )
            resolved_targets.append(
                ResolvedAssertionTarget(
                    assertion_id=assertion.id,
                    endpoint_role=endpoint_role,
                    target=target,
                )
            )

    return resolved_targets


def resolve_target(
    config: PreflightConfig,
    endpoint: Endpoint,
    session_factory: SessionFactory | None = None,
    ec2_client: Any | None = None,
    effective_account_id: str | None = None,
) -> ResolvedTarget:
    """Resolve one endpoint into a concrete AWS target."""

    account = endpoint.account
    region = config.defaults.region
    selector = endpoint.selector

    if ec2_client is None:
        if session_factory is None:
            session_factory = SessionFactory(config.defaults, config.accounts)

        session = session_factory.session_for_account(account, region=region)
        ec2_client = session.client("ec2")
        effective_account_id = session_factory.account_id_for_account(account, region=region)

    if selector.resource_id is not None:
        selector_type: SelectorType = "resource_id"
        discovered_target = resolve_by_resource_id(
            ec2_client=ec2_client,
            account=account,
            region=region,
            resource_id=selector.resource_id,
        )
    elif selector.arn is not None:
        selector_type = "arn"
        discovered_target = resolve_by_arn(
            ec2_client=ec2_client,
            account=account,
            region=region,
            arn=selector.arn,
            effective_account_id=effective_account_id,
        )
    elif selector.tags is not None:
        selector_type = "tags"
        discovered_target = resolve_by_tags(
            ec2_client=ec2_client,
            account=account,
            region=region,
            tags=selector.tags,
        )
    else:
        raise SelectorResolutionError(
            "Selector must define exactly one of resource_id, arn, or tags"
        )

    return _normalize_discovered_target(
        selector=selector,
        selector_type=selector_type,
        discovered_target=discovered_target,
    )


def resolve_by_resource_id(
    ec2_client: Any,
    account: str,
    region: str,
    resource_id: str,
) -> DiscoveredTarget:
    """Resolve a supported resource ID."""

    if ENI_ID_PATTERN.fullmatch(resource_id):
        return _resolve_eni(
            ec2_client=ec2_client,
            account=account,
            region=region,
            eni_id=resource_id,
        )

    if INSTANCE_ID_PATTERN.fullmatch(resource_id):
        return _resolve_instance(
            ec2_client=ec2_client,
            account=account,
            region=region,
            instance_id=resource_id,
        )

    raise SelectorResolutionError(
        f"Unsupported resource_id pattern '{resource_id}'. "
        "Supported v1 resource IDs are EC2 instances (i-...) and ENIs (eni-...)."
    )


def resolve_by_arn(
    ec2_client: Any,
    account: str,
    region: str,
    arn: str,
    effective_account_id: str | None = None,
) -> DiscoveredTarget:
    """Resolve a supported EC2 ARN."""

    parsed_arn = _parse_ec2_arn(arn)

    if parsed_arn["partition"] != AWS_PARTITION:
        raise SelectorResolutionError(
            f"ARN '{arn}' uses partition '{parsed_arn['partition']}', but v1 only supports "
            f"the standard commercial AWS partition '{AWS_PARTITION}'."
        )

    if parsed_arn["region"] != region:
        raise SelectorResolutionError(
            f"ARN '{arn}' is in region {parsed_arn['region']}, but v1 only supports "
            f"the effective region {region}."
        )

    expected_account_id = effective_account_id
    if expected_account_id is None:
        expected_account_id = _configured_account_id(ec2_client)
    if expected_account_id is None:
        raise SelectorResolutionError(
            "ARN-based resolution requires a reliable effective AWS account ID. "
            "Use the normal account-aware CLI/config flow or pass effective_account_id "
            "when calling resolve_target() directly."
        )

    if expected_account_id is not None and parsed_arn["account_id"] != expected_account_id:
        raise SelectorResolutionError(
            f"ARN '{arn}' is for account {parsed_arn['account_id']}, but endpoint account "
            f"'{account}' resolves in account {expected_account_id}."
        )

    resource_type = parsed_arn["resource_type"]
    resource_id = parsed_arn["resource_id"]

    if resource_type == "network-interface":
        return _resolve_eni(
            ec2_client=ec2_client,
            account=account,
            region=region,
            eni_id=resource_id,
        )

    if resource_type == "instance":
        return _resolve_instance(
            ec2_client=ec2_client,
            account=account,
            region=region,
            instance_id=resource_id,
        )

    raise SelectorResolutionError(
        f"Unsupported ARN resource type '{resource_type}' in '{arn}'. "
        "Supported v1 ARN resource types are 'instance' and 'network-interface'."
    )


def resolve_by_tags(
    ec2_client: Any,
    account: str,
    region: str,
    tags: dict[str, str],
) -> DiscoveredTarget:
    """Resolve a supported target by tags."""

    discovered_targets = _discover_enis_by_tags(
        ec2_client=ec2_client,
        account=account,
        region=region,
        tags=tags,
    ) + _discover_instances_by_tags(
        ec2_client=ec2_client,
        account=account,
        region=region,
        tags=tags,
    )

    for candidate in discovered_targets:
        _validate_supported_candidate(candidate)

    if not discovered_targets:
        raise SelectorResolutionError(
            f"No supported v1 targets matched selector.tags in account '{account}' "
            f"and region '{region}'."
        )

    if len(discovered_targets) > 1:
        candidate_list = ", ".join(_format_candidate(candidate) for candidate in discovered_targets)
        raise SelectorResolutionError(
            "selector.tags matched multiple supported v1 targets in account "
            f"'{account}' and region '{region}': {candidate_list}. "
            "v1 enforces tag uniqueness before normalization, even if multiple matches would "
            "normalize to the same ENI."
        )

    return discovered_targets[0]


def _resolve_eni(
    ec2_client: Any,
    account: str,
    region: str,
    eni_id: str,
) -> DiscoveredTarget:
    """Resolve one ENI by ID."""

    try:
        response = ec2_client.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
    except ClientError as exc:
        if _client_error_code(exc) == "InvalidNetworkInterfaceID.NotFound":
            raise SelectorResolutionError(
                f"No ENI matched resource_id '{eni_id}' in account "
                f"'{account}' and region '{region}'."
            ) from exc
        raise

    interfaces = response.get("NetworkInterfaces", [])
    if not interfaces:
        raise SelectorResolutionError(
            f"No ENI matched resource_id '{eni_id}' in account '{account}' and region '{region}'."
        )

    if len(interfaces) > 1:
        raise SelectorResolutionError(
            f"Multiple ENIs matched resource_id '{eni_id}' in account "
            f"'{account}' and region '{region}'."
        )

    interface = interfaces[0]
    owner_id = _require_str(
        interface.get("OwnerId"),
        f"ENI '{eni_id}' did not include OwnerId in the EC2 response.",
    )
    arn = _resolve_eni_arn(
        interface=interface,
        region=region,
        account_id=owner_id,
        eni_id=eni_id,
        error_context=f"ENI '{eni_id}' could not be mapped to an ARN.",
    )

    return DiscoveredTarget(
        account=account,
        region=region,
        target_type="eni",
        resource_id=eni_id,
        arn=arn,
        metadata={
            "owner_id": owner_id,
        },
    )


def _resolve_instance(
    ec2_client: Any,
    account: str,
    region: str,
    instance_id: str,
) -> DiscoveredTarget:
    """Resolve one EC2 instance by ID."""

    try:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
    except ClientError as exc:
        if _client_error_code(exc) == "InvalidInstanceID.NotFound":
            raise SelectorResolutionError(
                f"No EC2 instance matched resource_id '{instance_id}' in account "
                f"'{account}' and region '{region}'."
            ) from exc
        raise

    instances = _flatten_instances(response)
    if not instances:
        raise SelectorResolutionError(
            f"No EC2 instance matched resource_id '{instance_id}' in account "
            f"'{account}' and region '{region}'."
        )

    if len(instances) > 1:
        raise SelectorResolutionError(
            f"Multiple EC2 instances matched resource_id '{instance_id}' in account "
            f"'{account}' and region '{region}'."
        )

    owner_id, instance = instances[0]
    primary_eni = _extract_primary_eni(instance, instance_id)
    primary_eni_id = _require_str(
        primary_eni.get("NetworkInterfaceId"),
        f"Instance '{instance_id}' primary network interface is missing NetworkInterfaceId.",
    )
    instance_arn = _build_ec2_arn(
        region=region,
        account_id=owner_id,
        resource_type="instance",
        resource_id=instance_id,
    )
    primary_eni_arn = _build_ec2_arn(
        region=region,
        account_id=owner_id,
        resource_type="network-interface",
        resource_id=primary_eni_id,
    )

    return DiscoveredTarget(
        account=account,
        region=region,
        target_type="ec2_instance",
        resource_id=instance_id,
        arn=instance_arn,
        metadata={
            "owner_id": owner_id,
            "primary_eni_id": primary_eni_id,
            "primary_eni_arn": primary_eni_arn,
        },
    )


def _discover_enis_by_tags(
    ec2_client: Any,
    account: str,
    region: str,
    tags: dict[str, str],
) -> list[DiscoveredTarget]:
    """Discover ENIs that match the provided tag set."""
    matches: list[DiscoveredTarget] = []

    for response in _paginate_ec2(
        ec2_client,
        "describe_network_interfaces",
        Filters=_tag_filters(tags),
    ):
        for interface in response.get("NetworkInterfaces", []):
            if not isinstance(interface, dict):
                raise SelectorResolutionError("A matched ENI returned an invalid response shape.")
            eni_id = _require_str(
                interface.get("NetworkInterfaceId"),
                "A matched ENI is missing NetworkInterfaceId.",
            )
            owner_id = _require_str(
                interface.get("OwnerId"),
                f"Matched ENI '{eni_id}' is missing OwnerId.",
            )
            arn = _resolve_eni_arn(
                interface=interface,
                region=region,
                account_id=owner_id,
                eni_id=eni_id,
                error_context=f"Matched ENI '{eni_id}' could not be mapped to an ARN.",
            )
            matches.append(
                DiscoveredTarget(
                    account=account,
                    region=region,
                    target_type="eni",
                    resource_id=eni_id,
                    arn=arn,
                    metadata={"owner_id": owner_id},
                )
            )

    return matches


def _discover_instances_by_tags(
    ec2_client: Any,
    account: str,
    region: str,
    tags: dict[str, str],
) -> list[DiscoveredTarget]:
    """Discover instances that match the provided tag set."""
    matches: list[DiscoveredTarget] = []

    for response in _paginate_ec2(
        ec2_client,
        "describe_instances",
        Filters=_tag_filters(tags),
    ):
        for owner_id, instance in _flatten_instances(response):
            instance_id = _require_str(
                instance.get("InstanceId"),
                "A matched EC2 instance is missing InstanceId.",
            )
            primary_eni = _extract_primary_eni(instance, instance_id)
            primary_eni_id = _require_str(
                primary_eni.get("NetworkInterfaceId"),
                f"Matched EC2 instance '{instance_id}' primary network interface "
                "is missing NetworkInterfaceId.",
            )
            matches.append(
                DiscoveredTarget(
                    account=account,
                    region=region,
                    target_type="ec2_instance",
                    resource_id=instance_id,
                    arn=_build_ec2_arn(
                        region=region,
                        account_id=owner_id,
                        resource_type="instance",
                        resource_id=instance_id,
                    ),
                    metadata={
                        "owner_id": owner_id,
                        "primary_eni_id": primary_eni_id,
                        "primary_eni_arn": _build_ec2_arn(
                            region=region,
                            account_id=owner_id,
                            resource_type="network-interface",
                            resource_id=primary_eni_id,
                        ),
                    },
                )
            )

    return matches


def _normalize_discovered_target(
    selector: Selector,
    selector_type: SelectorType,
    discovered_target: DiscoveredTarget,
) -> ResolvedTarget:
    """Normalize a discovered target into the canonical v1 ENI execution target."""

    _validate_supported_candidate(discovered_target)

    if discovered_target.target_type == "eni":
        return ResolvedTarget(
            account=discovered_target.account,
            region=discovered_target.region,
            selector=selector,
            selector_type=selector_type,
            input_type="eni",
            input_identifier=discovered_target.resource_id,
            input_arn=discovered_target.arn,
            resolved_target_type="eni",
            resolved_identifier=discovered_target.resource_id,
            resolved_arn=discovered_target.arn,
            normalized=False,
            metadata=dict(discovered_target.metadata),
        )

    primary_eni_id = _require_str(
        discovered_target.metadata.get("primary_eni_id"),
        f"EC2 instance '{discovered_target.resource_id}' did not resolve to a primary ENI.",
    )
    primary_eni_arn = _require_str(
        discovered_target.metadata.get("primary_eni_arn"),
        f"EC2 instance '{discovered_target.resource_id}' did not resolve to a primary ENI ARN.",
    )

    metadata = dict(discovered_target.metadata)
    metadata["normalized_from_type"] = "ec2_instance"
    metadata["normalized_to_type"] = "eni"

    return ResolvedTarget(
        account=discovered_target.account,
        region=discovered_target.region,
        selector=selector,
        selector_type=selector_type,
        input_type="ec2_instance",
        input_identifier=discovered_target.resource_id,
        input_arn=discovered_target.arn,
        resolved_target_type="eni",
        resolved_identifier=primary_eni_id,
        resolved_arn=primary_eni_arn,
        normalized=True,
        metadata=metadata,
    )


def _paginate_ec2(
    ec2_client: Any,
    operation_name: Literal["describe_network_interfaces", "describe_instances"],
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Return all pages for an EC2 describe operation."""

    if hasattr(ec2_client, "get_paginator"):
        paginator = ec2_client.get_paginator(operation_name)
        return list(paginator.paginate(**kwargs))

    operation = getattr(ec2_client, operation_name)
    pages: list[dict[str, Any]] = []
    next_token: str | None = None

    while True:
        request_kwargs = dict(kwargs)
        if next_token is not None:
            request_kwargs["NextToken"] = next_token

        response = operation(**request_kwargs)
        pages.append(response)

        token = response.get("NextToken")
        if not isinstance(token, str) or not token:
            break
        next_token = token

    return pages


def _configured_account_id(ec2_client: Any) -> str | None:
    """Return the effective account ID when the client exposes it."""

    account_id = getattr(ec2_client, "account_id", None)
    if isinstance(account_id, str) and account_id:
        return account_id

    return None


def _resolve_eni_arn(
    interface: dict[str, Any],
    region: str,
    account_id: str,
    eni_id: str,
    error_context: str,
) -> str:
    """Return a canonical ENI ARN from response data or a local fallback."""

    raw_arn = interface.get("NetworkInterfaceArn")
    if raw_arn is None:
        return _build_ec2_arn(
            region=region,
            account_id=account_id,
            resource_type="network-interface",
            resource_id=eni_id,
        )

    return _require_str(raw_arn, error_context)


def _flatten_instances(response: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Flatten EC2 reservations into owner-aware instance tuples."""

    instances: list[tuple[str, dict[str, Any]]] = []

    for reservation in response.get("Reservations", []):
        if not isinstance(reservation, dict):
            raise SelectorResolutionError("An EC2 reservation returned an invalid response shape.")
        owner_id = _require_str(
            reservation.get("OwnerId"),
            "An EC2 reservation is missing OwnerId.",
        )
        raw_instances = reservation.get("Instances", [])
        if not isinstance(raw_instances, list):
            raise SelectorResolutionError(
                f"EC2 reservation for owner '{owner_id}' returned an invalid Instances shape."
            )
        for instance in raw_instances:
            if not isinstance(instance, dict):
                raise SelectorResolutionError(
                    f"EC2 reservation for owner '{owner_id}' returned an invalid instance shape."
                )
            instances.append((owner_id, instance))

    return instances


def _extract_primary_eni(instance: dict[str, Any], instance_id: str) -> dict[str, Any]:
    """Return the single primary ENI for an instance."""

    raw_interfaces = instance.get("NetworkInterfaces")
    if not isinstance(raw_interfaces, list):
        raise SelectorResolutionError(
            f"EC2 instance '{instance_id}' returned an invalid NetworkInterfaces shape."
        )

    if not raw_interfaces:
        raise SelectorResolutionError(
            f"EC2 instance '{instance_id}' did not include any network interfaces."
        )

    primary_interfaces: list[dict[str, Any]] = []
    for interface in raw_interfaces:
        if not isinstance(interface, dict):
            raise SelectorResolutionError(
                f"EC2 instance '{instance_id}' returned an invalid network interface shape."
            )

        attachment = interface.get("Attachment")
        if attachment is not None and not isinstance(attachment, dict):
            raise SelectorResolutionError(
                f"EC2 instance '{instance_id}' returned an invalid Attachment shape "
                "for a network interface."
            )

        device_index = None
        if attachment is not None:
            device_index = attachment.get("DeviceIndex")
            if device_index is not None and not isinstance(device_index, int):
                raise SelectorResolutionError(
                    f"EC2 instance '{instance_id}' returned an invalid DeviceIndex value "
                    "for a network interface attachment."
                )

        if device_index == 0:
            primary_interfaces.append(interface)

    if len(primary_interfaces) != 1:
        raise SelectorResolutionError(
            f"EC2 instance '{instance_id}' did not resolve to exactly one primary ENI."
        )

    return primary_interfaces[0]


def _tag_filters(tags: dict[str, str]) -> list[dict[str, Any]]:
    """Build EC2 tag filters."""

    return [{"Name": f"tag:{key}", "Values": [value]} for key, value in tags.items()]


def _parse_ec2_arn(arn: str) -> dict[str, str]:
    """Parse a supported EC2 ARN."""

    match = EC2_ARN_PATTERN.fullmatch(arn)
    if match is None:
        raise SelectorResolutionError(
            f"Unsupported ARN format '{arn}'. Supported v1 ARNs must be EC2 instance or "
            "network-interface ARNs."
        )

    return match.groupdict()


def _validate_supported_candidate(candidate: DiscoveredTarget) -> None:
    """Ensure a candidate belongs to a supported v1 target family."""

    if candidate.target_type not in {"eni", "ec2_instance"}:
        raise SelectorResolutionError(
            f"Matched unsupported v1 target type '{candidate.target_type}' for "
            f"resource '{candidate.resource_id}'."
        )


def _format_candidate(candidate: DiscoveredTarget) -> str:
    """Format a candidate for human-readable error messages."""

    return f"{candidate.target_type}:{candidate.resource_id}"


def _build_ec2_arn(region: str, account_id: str, resource_type: str, resource_id: str) -> str:
    """Build a canonical EC2 ARN."""

    return f"arn:{AWS_PARTITION}:ec2:{region}:{account_id}:{resource_type}/{resource_id}"


def _require_str(value: Any, error_message: str) -> str:
    """Return a required string value or raise a clear resolution error."""

    if isinstance(value, str) and value:
        return value

    raise SelectorResolutionError(error_message)


def _client_error_code(exc: ClientError) -> str | None:
    """Extract a botocore client error code."""

    code = exc.response.get("Error", {}).get("Code")
    if isinstance(code, str):
        return code

    return None
