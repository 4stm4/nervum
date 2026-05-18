"""Pure allocator: next_available_ip + is_address_assignable."""

from __future__ import annotations

import pytest

from sdn_controller.core.entities import Subnet
from sdn_controller.core.services.ip_allocator import (
    is_address_assignable,
    next_available_ip,
)
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import SubnetId
from sdn_controller.core.value_objects.ipam import IpRange


def _subnet(
    *,
    cidr: str = "10.0.0.0/24",
    gateway: str | None = "10.0.0.1",
    pools: tuple[IpRange, ...] = (),
    reserved: tuple[IpRange, ...] = (),
) -> Subnet:
    return Subnet(
        id=SubnetId("sub_1"),
        cidr=cidr,
        gateway=gateway,
        allocation_pools=pools,
        reserved_ranges=reserved,
    )


# ---------------------------------------------------------------------------
# next_available_ip
# ---------------------------------------------------------------------------


def test_pool_iteration_starts_after_gateway() -> None:
    sub = _subnet()

    assert next_available_ip(subnet=sub, in_use=()) == "10.0.0.2"


def test_skips_in_use_addresses() -> None:
    sub = _subnet()

    assert next_available_ip(subnet=sub, in_use=("10.0.0.2", "10.0.0.3")) == "10.0.0.4"


def test_respects_explicit_pools() -> None:
    sub = _subnet(pools=(IpRange(start="10.0.0.100", end="10.0.0.110"),))

    assert next_available_ip(subnet=sub, in_use=()) == "10.0.0.100"


def test_respects_reserved_ranges() -> None:
    sub = _subnet(reserved=(IpRange(start="10.0.0.2", end="10.0.0.10"),))

    assert next_available_ip(subnet=sub, in_use=()) == "10.0.0.11"


def test_exhausted_pool_raises_conflict() -> None:
    sub = _subnet(pools=(IpRange(start="10.0.0.100", end="10.0.0.101"),))

    with pytest.raises(ConflictError, match="no free addresses"):
        next_available_ip(subnet=sub, in_use=("10.0.0.100", "10.0.0.101"))


def test_skips_network_and_broadcast_when_no_pools() -> None:
    sub = _subnet(cidr="10.0.0.0/30", gateway=None)

    # In a /30, addresses are .0 (network), .1, .2, .3 (broadcast).
    # .1 and .2 are the only usable ones.
    first = next_available_ip(subnet=sub, in_use=())
    second = next_available_ip(subnet=sub, in_use=(first,))

    assert {first, second} == {"10.0.0.1", "10.0.0.2"}


# ---------------------------------------------------------------------------
# is_address_assignable
# ---------------------------------------------------------------------------


def test_assignable_inside_cidr_outside_reserved() -> None:
    sub = _subnet(reserved=(IpRange(start="10.0.0.10", end="10.0.0.20"),))

    is_address_assignable(subnet=sub, address="10.0.0.50")  # no raise


def test_assignable_gateway_rejected() -> None:
    sub = _subnet()

    with pytest.raises(ConflictError, match="gateway"):
        is_address_assignable(subnet=sub, address="10.0.0.1")


def test_assignable_outside_cidr_rejected() -> None:
    sub = _subnet()

    with pytest.raises(ValidationError, match="not inside subnet"):
        is_address_assignable(subnet=sub, address="192.168.0.1")


def test_assignable_reserved_rejected() -> None:
    sub = _subnet(reserved=(IpRange(start="10.0.0.10", end="10.0.0.20"),))

    with pytest.raises(ConflictError, match="reserved range"):
        is_address_assignable(subnet=sub, address="10.0.0.15")


def test_assignable_outside_pools_rejected() -> None:
    sub = _subnet(pools=(IpRange(start="10.0.0.100", end="10.0.0.200"),))

    with pytest.raises(ConflictError, match="not inside any allocation pool"):
        is_address_assignable(subnet=sub, address="10.0.0.50")
