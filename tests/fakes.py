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
        enis_by_id: dict[str, dict[str, Any]] | None = None,
        instances_by_id: dict[str, dict[str, Any]] | None = None,
        tag_enis: list[dict[str, Any]] | None = None,
        tag_instances: list[dict[str, Any]] | None = None,
    ) -> None:
        self.enis_by_id = enis_by_id or {}
        self.instances_by_id = instances_by_id or {}
        self.tag_enis = tag_enis or []
        self.tag_instances = tag_instances or []

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


class FakeSession:
    def __init__(self, ec2_client: FakeEC2Client) -> None:
        self._ec2_client = ec2_client

    def client(self, service_name: str) -> FakeEC2Client:
        if service_name != "ec2":
            raise AssertionError(f"Unexpected service request: {service_name}")
        return self._ec2_client


class FakeSessionFactory:
    def __init__(self, clients_by_account: dict[str, FakeEC2Client]) -> None:
        self._clients_by_account = clients_by_account

    def session_for_account(self, account_name: str, region: str | None = None) -> FakeSession:
        _ = region
        return FakeSession(self._clients_by_account[account_name])
