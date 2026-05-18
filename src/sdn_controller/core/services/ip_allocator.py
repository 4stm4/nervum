"""Pure IP allocator — pick the next free address from a subnet.

Inputs: the ``Subnet`` and the set of addresses already in use. Output: the
first available address that:

* sits inside one of the ``allocation_pools`` (or, if pools are empty,
  inside the subnet CIDR minus its network and broadcast addresses);
* is not the gateway;
* doesn't fall inside any ``reserved_ranges``;
* isn't already in the ``in_use`` set.

The allocator is a stateless function so it's trivially unit-testable and
can be re-run from any storage backend (in-memory, SQL, future Postgres).
The caller is responsible for atomicity — the use case fetches existing
allocations and writes the new one inside a single transaction.
"""

from __future__ import annotations

from collections.abc import Iterable
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from sdn_controller.core.entities import Subnet
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    ValidationError,
)
from sdn_controller.core.value_objects.ipam import IpRange

_ExhaustedT = type[ConflictError]
EXHAUSTED: _ExhaustedT = ConflictError

_IP_VERSION_4 = 4


def next_available_ip(
    *,
    subnet: Subnet,
    in_use: Iterable[str],
) -> str:
    """Return the lowest free address in ``subnet``.

    Raises ``ConflictError`` if no address is free. Reusing ``ConflictError``
    keeps the wire shape consistent — "exhausted" is what an operator sees
    when the same allocation would conflict with the laws of the subnet.
    """
    taken: set[int] = set()
    for raw in in_use:
        try:
            taken.add(int(ip_address(raw)))
        except ValueError:
            # Defensive: an allocation row with a malformed ip shouldn't
            # poison the allocator — skip it and keep going.
            continue

    for candidate in _iter_candidates(subnet):
        if int(candidate) in taken:
            continue
        return str(candidate)

    raise EXHAUSTED(f"subnet {subnet.cidr} has no free addresses")


def is_address_assignable(
    *,
    subnet: Subnet,
    address: str,
) -> None:
    """Validate a *specific* address against a subnet (used for reservations).

    Raises ``ValidationError`` if the address can't possibly live in the
    subnet, or ``ConflictError`` if it's structurally available but
    conflicts with the subnet's policy (gateway / reserved range / outside
    pools).
    """
    try:
        target = ip_address(address)
    except ValueError as exc:
        raise ValidationError(f"invalid ip address: {address}: {exc}") from exc

    net = ip_network(subnet.cidr, strict=True)
    if target not in net:
        raise ValidationError(
            f"address {address} is not inside subnet {subnet.cidr}",
        )
    if subnet.gateway is not None and ip_address(subnet.gateway) == target:
        raise ConflictError(f"address {address} is the subnet gateway")
    for rng in subnet.reserved_ranges:
        if rng.contains(address):
            raise ConflictError(
                f"address {address} is inside reserved range {rng.start}-{rng.end}",
            )
    if subnet.allocation_pools and not any(p.contains(address) for p in subnet.allocation_pools):
        raise ConflictError(
            f"address {address} is not inside any allocation pool",
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _iter_candidates(subnet: Subnet) -> Iterable[IPv4Address | IPv6Address]:
    """Iterate addresses that are *potentially* allocatable.

    Order is ascending so the allocator yields the same answer across runs
    given the same ``in_use`` set — useful for tests and for operator
    intuition ("first allocation is always .2 after the gateway").
    """
    net = ip_network(subnet.cidr, strict=True)
    pools: tuple[IpRange, ...] = subnet.allocation_pools or (
        IpRange(start=str(net.network_address + 1), end=str(net.broadcast_address - 1)),
    )

    gateway_int = int(ip_address(subnet.gateway)) if subnet.gateway is not None else None

    is_v4 = net.version == _IP_VERSION_4
    boundary_addresses = {net.network_address, net.broadcast_address}
    for pool in pools:
        start = int(ip_address(pool.start))
        end = int(ip_address(pool.end))
        for value in range(start, end + 1):
            address: IPv4Address | IPv6Address = IPv4Address(value) if is_v4 else IPv6Address(value)
            if address not in net:
                continue
            if address in boundary_addresses:
                continue
            if gateway_int is not None and int(address) == gateway_int:
                continue
            if any(rng.contains(str(address)) for rng in subnet.reserved_ranges):
                continue
            yield address
