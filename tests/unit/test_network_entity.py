"""Network and Subnet invariants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdn_controller.core.entities import Network, Subnet
from sdn_controller.core.value_objects.enums import NetworkType
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import NetworkId, SubnetId

_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def _net(**overrides: object) -> Network:
    base: dict[str, object] = {
        "id": NetworkId("net_1"),
        "name": "tenant-a",
        "type": NetworkType.VXLAN,
        "vni": 10100,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return Network(**base)  # type: ignore[arg-type]


def test_vxlan_network_requires_vni() -> None:
    with pytest.raises(ValidationError, match="vxlan network requires vni"):
        _net(vni=None)


def test_vxlan_network_rejects_vlan_id() -> None:
    with pytest.raises(ValidationError, match="must not set vlan_id"):
        _net(vlan_id=10)


def test_vlan_network_requires_vlan_id() -> None:
    with pytest.raises(ValidationError, match="vlan network requires vlan_id"):
        _net(type=NetworkType.VLAN, vni=None, vlan_id=None)


def test_vlan_id_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError, match="vlan_id 5000 out of range"):
        _net(type=NetworkType.VLAN, vni=None, vlan_id=5000)


def test_flat_network_rejects_vlan_and_vni() -> None:
    with pytest.raises(ValidationError, match="must not set vlan_id or vni"):
        _net(type=NetworkType.FLAT, vlan_id=10, vni=None)


def test_mtu_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError, match="mtu 0 out of range"):
        _net(mtu=0)


def test_empty_name_rejected() -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        _net(name="   ")


def test_subnet_validates_cidr() -> None:
    with pytest.raises(ValidationError, match="invalid cidr"):
        Subnet(id=SubnetId("sub_1"), cidr="not-a-cidr")


def test_subnet_gateway_must_be_inside_cidr() -> None:
    with pytest.raises(ValidationError, match="not inside subnet"):
        Subnet(id=SubnetId("sub_1"), cidr="10.0.0.0/24", gateway="192.168.0.1")


def test_subnet_accepts_valid_gateway() -> None:
    sub = Subnet(id=SubnetId("sub_1"), cidr="10.0.0.0/24", gateway="10.0.0.1")

    assert sub.gateway == "10.0.0.1"
