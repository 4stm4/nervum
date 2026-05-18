"""IPAM use cases: upsert a subnet, allocate / reserve / release IPs, list.

The allocator's atomicity story is "in-process": we read existing
allocations from the repo and write the new one back. Concurrent
allocations on the *same* subnet race on the underlying repository — the
SQL adapter's unique constraint on ``(subnet_id, ip_address)`` is the
backstop; the in-memory adapter doesn't see real concurrency.

For M6 that's enough — the controller is a single process. A future
worker-pool deployment will need either a leader-elected allocator or
``SELECT ... FOR UPDATE`` semantics; we leave that for the M11/HA
milestone rather than over-engineer now.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from sdn_controller.core.entities import (
    IpAllocation,
    Network,
    Subnet,
)
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.services.ip_allocator import (
    is_address_assignable,
    next_available_ip,
)
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    NotFoundError,
)
from sdn_controller.core.value_objects.ids import (
    IdFactory,
    IpAllocationId,
    NetworkId,
    SubnetId,
)
from sdn_controller.core.value_objects.ipam import (
    IpAllocationKind,
    IpRange,
    OwnerRef,
)
from sdn_controller.ports.persistence import (
    IpAllocationRepository,
    NetworkRepository,
)

# ---------------------------------------------------------------------------
# Commands / results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UpsertSubnetCommand:
    cidr: str
    gateway: str | None = None
    dns_servers: tuple[str, ...] = ()
    allocation_pools: tuple[IpRange, ...] = ()
    reserved_ranges: tuple[IpRange, ...] = ()


@dataclass(frozen=True, slots=True)
class AllocateIpCommand:
    owner: OwnerRef
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ReserveIpCommand:
    ip_address: str
    owner: OwnerRef
    label: str | None = None


@dataclass(frozen=True, slots=True)
class SubnetWithNetwork:
    network: Network
    subnet: Subnet


@dataclass(frozen=True, slots=True)
class AllocationResult:
    allocation: IpAllocation


# ---------------------------------------------------------------------------
# Subnet upsert
# ---------------------------------------------------------------------------


class UpsertSubnet:
    """Create or replace the subnet attached to a network.

    If the network already has a subnet, this **replaces** it (keeps the
    existing subnet id so allocations stay attached). Any existing
    allocations whose address no longer fits the new pools/cidr would
    silently become stranded — the M6 acceptance criteria expect cidr +
    pool validation here, but conflict-detection against existing leases
    lives in M11/cleanup. For now we refuse the upsert if a pre-existing
    allocation falls outside the new CIDR.
    """

    def __init__(
        self,
        *,
        networks: NetworkRepository,
        allocations: IpAllocationRepository,
        ids: IdFactory,
        clock: Clock,
    ) -> None:
        self._networks = networks
        self._allocations = allocations
        self._ids = ids
        self._clock = clock

    async def execute(self, network_id: NetworkId, cmd: UpsertSubnetCommand) -> Network:
        network = await self._networks.get(network_id)
        if network is None:
            raise NotFoundError(f"network {network_id} not found")

        previous = network.subnet
        subnet = Subnet(
            id=previous.id if previous is not None else self._ids.subnet(),
            cidr=cmd.cidr,
            gateway=cmd.gateway,
            dns_servers=tuple(cmd.dns_servers),
            allocation_pools=tuple(cmd.allocation_pools),
            reserved_ranges=tuple(cmd.reserved_ranges),
        )

        if previous is not None:
            existing = await self._allocations.list_for_subnet(previous.id)
            net = ip_network(subnet.cidr, strict=True)
            for alloc in existing:
                if ip_address(alloc.ip_address) not in net:
                    raise ConflictError(
                        f"upsert would strand allocation {alloc.id} "
                        f"({alloc.ip_address} no longer in {subnet.cidr})",
                    )

        network.subnet = subnet
        network.bump_intent(now=self._clock.now())
        await self._networks.save(network)
        return network


# ---------------------------------------------------------------------------
# Allocate / reserve / release / list
# ---------------------------------------------------------------------------


class AllocateIp:
    """Dynamically assign the next available IP from a subnet."""

    def __init__(
        self,
        *,
        networks: NetworkRepository,
        allocations: IpAllocationRepository,
        ids: IdFactory,
        clock: Clock,
    ) -> None:
        self._networks = networks
        self._allocations = allocations
        self._ids = ids
        self._clock = clock

    async def execute(self, subnet_id: SubnetId, cmd: AllocateIpCommand) -> IpAllocation:
        subnet = await _resolve_subnet(self._networks, subnet_id)

        existing = await self._allocations.list_for_subnet(subnet_id)
        in_use = [a.ip_address for a in existing]
        ip = next_available_ip(subnet=subnet, in_use=in_use)

        allocation = IpAllocation(
            id=self._ids.ip_allocation(),
            subnet_id=subnet_id,
            ip_address=ip,
            owner=cmd.owner,
            kind=IpAllocationKind.DYNAMIC,
            allocated_at=self._clock.now(),
            label=cmd.label,
        )
        await self._allocations.save(allocation)
        return allocation


class ReserveIp:
    """Pin a specific address. Fails if it's already taken or out of policy."""

    def __init__(
        self,
        *,
        networks: NetworkRepository,
        allocations: IpAllocationRepository,
        ids: IdFactory,
        clock: Clock,
    ) -> None:
        self._networks = networks
        self._allocations = allocations
        self._ids = ids
        self._clock = clock

    async def execute(self, subnet_id: SubnetId, cmd: ReserveIpCommand) -> IpAllocation:
        subnet = await _resolve_subnet(self._networks, subnet_id)
        is_address_assignable(subnet=subnet, address=cmd.ip_address)

        clash = await self._allocations.get_by_address(subnet_id, cmd.ip_address)
        if clash is not None:
            raise ConflictError(
                f"address {cmd.ip_address} is already allocated (allocation {clash.id})",
            )

        allocation = IpAllocation(
            id=self._ids.ip_allocation(),
            subnet_id=subnet_id,
            ip_address=cmd.ip_address,
            owner=cmd.owner,
            kind=IpAllocationKind.RESERVATION,
            allocated_at=self._clock.now(),
            label=cmd.label,
        )
        await self._allocations.save(allocation)
        return allocation


class ReleaseIp:
    """Drop an allocation. Idempotent — releasing twice is a no-op."""

    def __init__(self, *, allocations: IpAllocationRepository) -> None:
        self._allocations = allocations

    async def execute(self, allocation_id: IpAllocationId) -> None:
        await self._allocations.delete(allocation_id)


class ListAllocations:
    def __init__(
        self,
        *,
        networks: NetworkRepository,
        allocations: IpAllocationRepository,
    ) -> None:
        self._networks = networks
        self._allocations = allocations

    async def execute(self, subnet_id: SubnetId) -> list[IpAllocation]:
        # Make sure the subnet exists so callers get a clean 404 rather than
        # an empty list when they typo the id.
        await _resolve_subnet(self._networks, subnet_id)
        return await self._allocations.list_for_subnet(subnet_id)


class GetAllocation:
    def __init__(self, *, allocations: IpAllocationRepository) -> None:
        self._allocations = allocations

    async def execute(self, allocation_id: IpAllocationId) -> IpAllocation:
        allocation = await self._allocations.get(allocation_id)
        if allocation is None:
            raise NotFoundError(f"allocation {allocation_id} not found")
        return allocation


# ---------------------------------------------------------------------------
# Subnet read use case (for ``GET /subnets``/``GET /subnets/{id}``)
# ---------------------------------------------------------------------------


class ListSubnets:
    def __init__(self, *, networks: NetworkRepository) -> None:
        self._networks = networks

    async def execute(self) -> list[SubnetWithNetwork]:
        out: list[SubnetWithNetwork] = []
        for network in await self._networks.list():
            if network.subnet is not None:
                out.append(SubnetWithNetwork(network=network, subnet=network.subnet))
        return out


class GetSubnet:
    def __init__(self, *, networks: NetworkRepository) -> None:
        self._networks = networks

    async def execute(self, subnet_id: SubnetId) -> SubnetWithNetwork:
        network = await self._networks.get_by_subnet_id(subnet_id)
        if network is None or network.subnet is None:
            raise NotFoundError(f"subnet {subnet_id} not found")
        return SubnetWithNetwork(network=network, subnet=network.subnet)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_subnet(networks: NetworkRepository, subnet_id: SubnetId) -> Subnet:
    network = await networks.get_by_subnet_id(subnet_id)
    if network is None or network.subnet is None:
        raise NotFoundError(f"subnet {subnet_id} not found")
    return network.subnet


__all__ = [
    "AllocateIp",
    "AllocateIpCommand",
    "AllocationResult",
    "GetAllocation",
    "GetSubnet",
    "ListAllocations",
    "ListSubnets",
    "ReleaseIp",
    "ReserveIp",
    "ReserveIpCommand",
    "SubnetWithNetwork",
    "UpsertSubnet",
    "UpsertSubnetCommand",
]
