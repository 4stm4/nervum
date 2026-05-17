"""SQL adapter contract tests.

These tests exercise the SQLAlchemy-backed repositories against a real SQLite
database (per-test temp file). They are the safety net that lets us swap the
in-memory adapter for SQL without breaking use cases.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdn_controller.adapters.sql import (
    SqlNetworkRepository,
    SqlNodeRepository,
    SqlOperationRepository,
    build_engine,
    build_sessionmaker,
)
from sdn_controller.adapters.sql.models import Base
from sdn_controller.core.entities import (
    Network,
    Node,
    Operation,
    OperationError,
    ResourceRef,
    Subnet,
)
from sdn_controller.core.value_objects.enums import (
    NetworkType,
    NodeStatus,
    OperationKind,
    OperationStatus,
)
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import (
    NetworkId,
    NodeId,
    OperationId,
    SubnetId,
)

_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Fresh SQLite file per test — isolation without sharing-connection magic."""
    db = tmp_path / "sdn.db"
    engine = build_engine(f"sqlite+aiosqlite:///{db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def test_node_save_and_get(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    repo = SqlNodeRepository(sessionmaker)
    node = Node(
        id=NodeId("node_1"),
        name="edge-1",
        mgmt_ip="10.0.0.10",
        status=NodeStatus.ONLINE,
        created_at=_NOW,
        updated_at=_NOW,
        roles=["edge"],
        labels={"site": "dc1"},
        agent_version="0.1.0",
        last_seen_at=_NOW,
    )
    await repo.save(node)

    fetched = await repo.get(NodeId("node_1"))
    assert fetched is not None
    assert fetched.name == "edge-1"
    assert fetched.roles == ["edge"]
    assert fetched.labels == {"site": "dc1"}
    assert fetched.last_seen_at == _NOW
    assert fetched.created_at.tzinfo is not None  # tz preserved


async def test_node_get_missing_returns_none(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlNodeRepository(sessionmaker)

    assert await repo.get(NodeId("node_missing")) is None


async def test_node_list_orders_by_name(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlNodeRepository(sessionmaker)
    for name in ("b-node", "a-node", "c-node"):
        await repo.save(
            Node(
                id=NodeId(f"node_{name}"),
                name=name,
                mgmt_ip="10.0.0.1",
                created_at=_NOW,
                updated_at=_NOW,
            )
        )

    nodes = await repo.list()

    assert [n.name for n in nodes] == ["a-node", "b-node", "c-node"]


# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------


async def test_network_round_trip_with_subnet(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlNetworkRepository(sessionmaker)
    network = Network(
        id=NetworkId("net_1"),
        name="tenant-a",
        type=NetworkType.VXLAN,
        vni=10100,
        subnet=Subnet(id=SubnetId("sub_1"), cidr="10.100.0.0/24", gateway="10.100.0.1"),
        created_at=_NOW,
        updated_at=_NOW,
        labels={"tier": "prod"},
    )
    await repo.save(network)

    fetched = await repo.get(NetworkId("net_1"))

    assert fetched is not None
    assert fetched.name == "tenant-a"
    assert fetched.type is NetworkType.VXLAN
    assert fetched.vni == 10100
    assert fetched.subnet is not None
    assert fetched.subnet.cidr == "10.100.0.0/24"
    assert fetched.subnet.gateway == "10.100.0.1"
    assert fetched.labels == {"tier": "prod"}


async def test_network_get_by_name(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    repo = SqlNetworkRepository(sessionmaker)
    await repo.save(
        Network(
            id=NetworkId("net_1"),
            name="tenant-a",
            type=NetworkType.VLAN,
            vlan_id=10,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )

    fetched = await repo.get_by_name("tenant-a")

    assert fetched is not None
    assert fetched.id == "net_1"


async def test_network_delete_cascades_subnet(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlNetworkRepository(sessionmaker)
    await repo.save(
        Network(
            id=NetworkId("net_1"),
            name="tenant-a",
            type=NetworkType.VXLAN,
            vni=10100,
            subnet=Subnet(id=SubnetId("sub_1"), cidr="10.100.0.0/24"),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )

    await repo.delete(NetworkId("net_1"))

    assert await repo.get(NetworkId("net_1")) is None
    # Inserting a new network reusing the subnet id proves the FK cascade fired.
    await repo.save(
        Network(
            id=NetworkId("net_2"),
            name="tenant-b",
            type=NetworkType.VXLAN,
            vni=10200,
            subnet=Subnet(id=SubnetId("sub_1"), cidr="10.200.0.0/24"),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )


async def test_network_save_is_idempotent(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlNetworkRepository(sessionmaker)
    network = Network(
        id=NetworkId("net_1"),
        name="tenant-a",
        type=NetworkType.VLAN,
        vlan_id=10,
        created_at=_NOW,
        updated_at=_NOW,
    )
    await repo.save(network)
    await repo.save(network)  # second save must update, not duplicate

    assert len(await repo.list()) == 1


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def _make_op() -> Operation:
    op = Operation.accept(
        operation_id=OperationId("op_1"),
        kind=OperationKind.NETWORK_CREATE,
        resource=ResourceRef(type="network", id="net_1"),
        now=_NOW,
        created_by="alice",
    )
    for status, msg in (
        (OperationStatus.PLANNING, "plan"),
        (OperationStatus.RUNNING, "run"),
        (OperationStatus.VERIFYING, "verify"),
        (OperationStatus.SUCCEEDED, "done"),
    ):
        op.transition_to(status, now=_NOW, message=msg)
    return op


async def test_operation_save_with_events_round_trip(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlOperationRepository(sessionmaker)
    op = _make_op()

    await repo.save(op)
    fetched = await repo.get(OperationId("op_1"))

    assert fetched is not None
    assert fetched.status is OperationStatus.SUCCEEDED
    assert fetched.created_by == "alice"
    assert [e.status for e in fetched.events] == [
        OperationStatus.ACCEPTED,
        OperationStatus.PLANNING,
        OperationStatus.RUNNING,
        OperationStatus.VERIFYING,
        OperationStatus.SUCCEEDED,
    ]
    assert [e.sequence for e in fetched.events] == [1, 2, 3, 4, 5]


async def test_operation_save_with_error_round_trip(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlOperationRepository(sessionmaker)
    op = Operation.accept(
        operation_id=OperationId("op_err"),
        kind=OperationKind.NETWORK_APPLY,
        resource=ResourceRef(type="network", id="net_1"),
        now=_NOW,
    )
    op.transition_to(OperationStatus.PLANNING, now=_NOW, message="p")
    op.transition_to(OperationStatus.RUNNING, now=_NOW, message="r")
    op.transition_to(
        OperationStatus.FAILED,
        now=_NOW,
        message="agent unreachable",
        error=OperationError(code="agent_unreachable", message="node-1 offline"),
    )

    await repo.save(op)
    fetched = await repo.get(OperationId("op_err"))

    assert fetched is not None
    assert fetched.status is OperationStatus.FAILED
    assert fetched.error is not None
    assert fetched.error.code == "agent_unreachable"


async def test_operation_list_ordered_by_created_at_desc(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlOperationRepository(sessionmaker)
    for i, dt in enumerate(
        (
            datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC),
            datetime(2026, 5, 17, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 5, 17, 11, 0, 0, tzinfo=UTC),
        )
    ):
        op = Operation.accept(
            operation_id=OperationId(f"op_{i}"),
            kind=OperationKind.NETWORK_CREATE,
            resource=ResourceRef(type="network", id=f"net_{i}"),
            now=dt,
        )
        await repo.save(op)

    ops = await repo.list(limit=10)

    assert [o.id for o in ops] == ["op_1", "op_0", "op_2"]


async def test_operation_update_status_appends_event(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlOperationRepository(sessionmaker)
    op = Operation.accept(
        operation_id=OperationId("op_partial"),
        kind=OperationKind.NETWORK_CREATE,
        resource=ResourceRef(type="network", id="net_1"),
        now=_NOW,
    )
    await repo.save(op)

    op.transition_to(OperationStatus.PLANNING, now=_NOW, message="plan")
    await repo.update_status(op.id, op.status, op.events[-1])

    fetched = await repo.get(OperationId("op_partial"))

    assert fetched is not None
    assert fetched.status is OperationStatus.PLANNING
    assert [e.sequence for e in fetched.events] == [1, 2]


async def test_operation_update_status_missing_raises_not_found(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlOperationRepository(sessionmaker)
    op = _make_op()

    with pytest.raises(NotFoundError):
        await repo.update_status(OperationId("op_ghost"), op.status, op.events[0])
