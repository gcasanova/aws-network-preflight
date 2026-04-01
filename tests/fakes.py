from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError


def make_eni(
    eni_id: str,
    *,
    owner_id: str = "222222222222",
    region: str = "us-east-1",
) -> dict[str, Any]:
    return {
        "NetworkInterfaceId": eni_id,
        "OwnerId": owner_id,
        "NetworkInterfaceArn": f"arn:aws:ec2:{region}:{owner_id}:network-interface/{eni_id}",
    }


def make_instance(
    instance_id: str,
    *,
    primary_eni_id: str,
    owner_id: str = "222222222222",
) -> dict[str, Any]:
    return {
        "OwnerId": owner_id,
        "Instances": [
            {
                "InstanceId": instance_id,
                "NetworkInterfaces": [
                    {
                        "NetworkInterfaceId": primary_eni_id,
                        "Attachment": {"DeviceIndex": 0},
                    }
                ],
            }
        ],
    }


class FakeEC2Client:
    def __init__(
        self,
        *,
        account_id: str | None = "222222222222",
        enis_by_id: dict[str, dict[str, Any]] | None = None,
        instances_by_id: dict[str, dict[str, Any]] | None = None,
        tag_enis: list[dict[str, Any]] | None = None,
        tag_instances: list[dict[str, Any]] | None = None,
        tag_eni_pages: list[list[dict[str, Any]]] | None = None,
        tag_instance_pages: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.account_id = account_id
        self.enis_by_id = enis_by_id or {}
        self.instances_by_id = instances_by_id or {}
        self.tag_enis = tag_enis or []
        self.tag_instances = tag_instances or []
        self.tag_eni_pages = tag_eni_pages
        self.tag_instance_pages = tag_instance_pages

    def get_paginator(self, operation_name: str) -> FakePaginator:
        if operation_name == "describe_network_interfaces":
            return FakePaginator.network_interfaces(self.tag_eni_pages or [self.tag_enis])
        if operation_name == "describe_instances":
            return FakePaginator.instances(self.tag_instance_pages or [self.tag_instances])
        raise AssertionError(f"Unexpected paginator request: {operation_name}")

    def describe_network_interfaces(self, **kwargs: Any) -> dict[str, Any]:
        if "NetworkInterfaceIds" in kwargs:
            eni_id = kwargs["NetworkInterfaceIds"][0]
            eni = self.enis_by_id.get(eni_id)
            if eni is None:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "InvalidNetworkInterfaceID.NotFound",
                            "Message": f"The networkInterface ID '{eni_id}' does not exist",
                        }
                    },
                    "DescribeNetworkInterfaces",
                )
            return {"NetworkInterfaces": [eni]}

        return {"NetworkInterfaces": list(self.tag_enis)}

    def describe_instances(self, **kwargs: Any) -> dict[str, Any]:
        if "InstanceIds" in kwargs:
            instance_id = kwargs["InstanceIds"][0]
            reservation = self.instances_by_id.get(instance_id)
            if reservation is None:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "InvalidInstanceID.NotFound",
                            "Message": f"The instance ID '{instance_id}' does not exist",
                        }
                    },
                    "DescribeInstances",
                )
            return {"Reservations": [reservation]}

        return {"Reservations": list(self.tag_instances)}


class FakeSTSClient:
    def __init__(
        self,
        *,
        account_id: str | None = "222222222222",
        raise_error: Exception | None = None,
    ) -> None:
        self.account_id = account_id
        self.raise_error = raise_error
        self.get_caller_identity_calls = 0

    def get_caller_identity(self) -> dict[str, Any]:
        self.get_caller_identity_calls += 1
        if self.raise_error is not None:
            raise self.raise_error
        if self.account_id is None:
            return {}
        return {"Account": self.account_id}


class FakePaginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    @classmethod
    def network_interfaces(cls, pages: list[list[dict[str, Any]]]) -> FakePaginator:
        return cls([{"NetworkInterfaces": page} for page in pages])

    @classmethod
    def instances(cls, pages: list[list[dict[str, Any]]]) -> FakePaginator:
        return cls([{"Reservations": page} for page in pages])

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        _ = kwargs
        return list(self._pages)


class FakeSession:
    def __init__(self, ec2_client: FakeEC2Client, sts_client: FakeSTSClient | None = None) -> None:
        self._ec2_client = ec2_client
        self._sts_client = sts_client or FakeSTSClient(account_id=ec2_client.account_id)

    def client(self, service_name: str) -> Any:
        if service_name == "ec2":
            return self._ec2_client
        if service_name == "sts":
            return self._sts_client
        raise AssertionError(f"Unexpected service request: {service_name}")


class FakeSessionFactory:
    def __init__(
        self,
        clients_by_account: dict[str, FakeEC2Client],
        *,
        sts_clients_by_account: dict[str, FakeSTSClient] | None = None,
    ) -> None:
        self._clients_by_account = clients_by_account
        self._sts_clients_by_account = sts_clients_by_account or {}
        self._account_id_cache: dict[str, str] = {}

    def session_for_account(self, account_name: str, region: str | None = None) -> FakeSession:
        _ = region
        return FakeSession(
            self._clients_by_account[account_name],
            self._sts_clients_by_account.get(account_name),
        )

    def account_id_for_account(self, account_name: str, region: str | None = None) -> str:
        _ = region
        cached_account_id = self._account_id_cache.get(account_name)
        if cached_account_id is not None:
            return cached_account_id

        session = self.session_for_account(account_name)
        response = session.client("sts").get_caller_identity()
        account_id = response.get("Account")
        if not isinstance(account_id, str) or not account_id:
            raise AssertionError(f"Fake STS client did not return an account ID for {account_name}")

        self._account_id_cache[account_name] = account_id
        return account_id
