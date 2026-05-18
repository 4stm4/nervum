"""SQL adapter coverage for IPAM: subnet round-trip and allocations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdn_controller.adapters.sql import (
    SqlIpAllocationRepository,
    SqlNetworkRepository,
    build_engine,
    build_sessionmaker,
)
from sdn_controller.adapters.sql.models import Base
from sdn_controller.core.entities import IpAllocation, Network, Subnet
from sdn_controller.core.value_objects.enums import NetworkType
from sdn_controller.core.value_objects.ids import (
    IpAllocationId,
    NetworkId,
    SubnetId,
)
from sdn_controller.core.value_objects.ipam import (
    IpAllocationKind,
    IpRange,
    OwnerRef,
)

_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db = tmp_path / "sdn.db"
    engine = build_engine(f"sqlite+aiosqlite:///{db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _persist_network_with_subnet(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[Network, Subnet]:
    networks = SqlNetworkRepository(sessionmaker)
    subnet = Subnet(
        id=SubnetId("sub_1"),
        cidr="10.0.0.0/24",
        gateway="10.0.0.1",
        dns_servers=("10.0.0.10",),
        allocation_pools=(IpRange(start="10.0.0.100", end="10.0.0.200"),),
        reserved_ranges=(IpRange(start="10.0.0.50", end="10.0.0.60"),),
    )
    network = Network(
        id=NetworkId("net_1"),
        name="prod",
        type=NetworkType.VXLAN,
        vni=10100,
        created_at=_NOW,
        updated_at=_NOW,
        subnet=subnet,
    )
    await networks.save(network)
    return network, subnet


async def test_subnet_round_trips_ipam_fields(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _persist_network_with_subnet(sessionmaker)
    networks = SqlNetworkRepository(sessionmaker)

    network = await networks.get_by_subnet_id(SubnetId("sub_1"))

    assert network is not None
    assert network.subnet is not None
    assert network.subnet.dns_servers == ("10.0.0.10",)
    assert network.subnet.allocation_pools == (IpRange(start="10.0.0.100", end="10.0.0.200"),)
    assert network.subnet.reserved_ranges == (IpRange(start="10.0.0.50", end="10.0.0.60"),)


async def test_allocation_round_trip(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _persist_network_with_subnet(sessionmaker)
    allocations = SqlIpAllocationRepository(sessionmaker)
    allocation = IpAllocation(
        id=IpAllocationId("ipa_1"),
        subnet_id=SubnetId("sub_1"),
        ip_address="10.0.0.42",
        owner=OwnerRef(type="vm-port", id="vm-1"),
        kind=IpAllocationKind.RESERVATION,
        allocated_at=_NOW,
        label="primary",
    )

    await allocations.save(allocation)
    fetched = await allocations.get(IpAllocationId("ipa_1"))

    assert fetched is not None
    assert fetched.owner == OwnerRef(type="vm-port", id="vm-1")
    assert fetched.kind is IpAllocationKind.RESERVATION
    assert fetched.label == "primary"


async def test_unique_constraint_blocks_double_address(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _persist_network_with_subnet(sessionmaker)
    allocations = SqlIpAllocationRepository(sessionmaker)
    await allocations.save(
        IpAllocation(
            id=IpAllocationId("ipa_1"),
            subnet_id=SubnetId("sub_1"),
            ip_address="10.0.0.42",
            owner=OwnerRef(type="vm-port", id="vm-1"),
            kind=IpAllocationKind.RESERVATION,
            allocated_at=_NOW,
        )
    )

    with pytest.raises(IntegrityError):
        await allocations.save(
            IpAllocation(
                id=IpAllocationId("ipa_2"),
                subnet_id=SubnetId("sub_1"),
                ip_address="10.0.0.42",
                owner=OwnerRef(type="vm-port", id="vm-2"),
                kind=IpAllocationKind.RESERVATION,
                allocated_at=_NOW,
            )
        )


async def test_list_for_owner_and_subnet(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _persist_network_with_subnet(sessionmaker)
    allocations = SqlIpAllocationRepository(sessionmaker)
    owner = OwnerRef(type="vm-port", id="vm-1")
    for idx, ip in enumerate(("10.0.0.10", "10.0.0.11", "10.0.0.12"), start=1):
        await allocations.save(
            IpAllocation(
                id=IpAllocationId(f"ipa_{idx}"),
                subnet_id=SubnetId("sub_1"),
                ip_address=ip,
                owner=owner if ip != "10.0.0.12" else OwnerRef(type="other", id="x"),
                kind=IpAllocationKind.DYNAMIC,
                allocated_at=_NOW,
            )
        )

    by_owner = await allocations.list_for_owner(owner)
    by_subnet = await allocations.list_for_subnet(SubnetId("sub_1"))

    assert [a.ip_address for a in by_owner] == ["10.0.0.10", "10.0.0.11"]
    assert {a.ip_address for a in by_subnet} == {"10.0.0.10", "10.0.0.11", "10.0.0.12"}


async def test_delete_subnet_cascades_to_allocations(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _persist_network_with_subnet(sessionmaker)
    allocations = SqlIpAllocationRepository(sessionmaker)
    await allocations.save(
        IpAllocation(
            id=IpAllocationId("ipa_1"),
            subnet_id=SubnetId("sub_1"),
            ip_address="10.0.0.42",
            owner=OwnerRef(type="vm-port", id="vm-1"),
            kind=IpAllocationKind.DYNAMIC,
            allocated_at=_NOW,
        )
    )

    networks = SqlNetworkRepository(sessionmaker)
    await networks.delete(NetworkId("net_1"))

    assert await allocations.get(IpAllocationId("ipa_1")) is None
