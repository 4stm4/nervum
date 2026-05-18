"""Subnet invariants for the IPAM extras."""

from __future__ import annotations

import pytest

from sdn_controller.core.entities import Subnet
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import SubnetId
from sdn_controller.core.value_objects.ipam import IpRange


def _subnet(**overrides: object) -> Subnet:
    base: dict[str, object] = {
        "id": SubnetId("sub_1"),
        "cidr": "10.0.0.0/24",
        "gateway": "10.0.0.1",
    }
    base.update(overrides)
    return Subnet(**base)  # type: ignore[arg-type]


def test_pool_outside_cidr_rejected() -> None:
    with pytest.raises(ValidationError, match="not inside subnet"):
        _subnet(allocation_pools=(IpRange(start="192.168.0.10", end="192.168.0.20"),))


def test_reserved_outside_cidr_rejected() -> None:
    with pytest.raises(ValidationError, match="not inside subnet"):
        _subnet(reserved_ranges=(IpRange(start="11.0.0.1", end="11.0.0.5"),))


def test_overlapping_pools_rejected() -> None:
    with pytest.raises(ValidationError, match="allocation pools overlap"):
        _subnet(
            allocation_pools=(
                IpRange(start="10.0.0.100", end="10.0.0.150"),
                IpRange(start="10.0.0.140", end="10.0.0.200"),
            ),
        )


def test_gateway_inside_pool_rejected() -> None:
    with pytest.raises(ValidationError, match="lies inside allocation pool"):
        _subnet(
            gateway="10.0.0.100",
            allocation_pools=(IpRange(start="10.0.0.50", end="10.0.0.150"),),
        )


def test_invalid_dns_server_rejected() -> None:
    with pytest.raises(ValidationError, match="invalid dns server"):
        _subnet(dns_servers=("not-an-ip",))


def test_valid_subnet_with_pools_dns_and_reserved() -> None:
    sub = _subnet(
        dns_servers=("10.0.0.10", "10.0.0.11"),
        allocation_pools=(IpRange(start="10.0.0.100", end="10.0.0.200"),),
        reserved_ranges=(IpRange(start="10.0.0.20", end="10.0.0.30"),),
    )

    assert sub.dns_servers == ("10.0.0.10", "10.0.0.11")
    assert sub.allocation_pools[0].contains("10.0.0.150")
    assert sub.reserved_ranges[0].contains("10.0.0.25")


def test_ip_range_overlap_and_contains() -> None:
    a = IpRange(start="10.0.0.1", end="10.0.0.10")
    b = IpRange(start="10.0.0.5", end="10.0.0.15")
    c = IpRange(start="10.0.0.11", end="10.0.0.20")

    assert a.overlaps(b)
    assert not a.overlaps(c)
    assert a.contains("10.0.0.5")
    assert not a.contains("10.0.0.11")


def test_ip_range_rejects_inverted_endpoints() -> None:
    with pytest.raises(ValidationError, match="start > end"):
        IpRange(start="10.0.0.20", end="10.0.0.10")


def test_ip_range_rejects_mixed_families() -> None:
    with pytest.raises(ValidationError, match="mixes address families"):
        IpRange(start="10.0.0.1", end="::1")
