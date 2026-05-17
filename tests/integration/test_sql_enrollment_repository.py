"""SQL adapter coverage for the enrollment-token repository + node capabilities."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdn_controller.adapters.sql import (
    SqlEnrollmentTokenRepository,
    SqlNodeRepository,
    build_engine,
    build_sessionmaker,
)
from sdn_controller.adapters.sql.models import Base
from sdn_controller.core.entities import EnrollmentToken, Node
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.enums import NodeStatus
from sdn_controller.core.value_objects.ids import EnrollmentTokenId, NodeId

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


async def test_node_capabilities_round_trip(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlNodeRepository(sessionmaker)
    node = Node(
        id=NodeId("node_1"),
        name="edge-1",
        mgmt_ip="10.0.0.10",
        status=NodeStatus.ONLINE,
        created_at=_NOW,
        updated_at=_NOW,
        capabilities=NodeCapabilities(
            ovs_version="3.2.1",
            kernel="6.6.0",
            interfaces=("eth0", "eth1"),
            features=("vxlan", "stp"),
        ),
    )
    await repo.save(node)

    fetched = await repo.get(NodeId("node_1"))

    assert fetched is not None
    assert fetched.capabilities is not None
    assert fetched.capabilities.ovs_version == "3.2.1"
    assert fetched.capabilities.interfaces == ("eth0", "eth1")
    assert fetched.capabilities.features == ("vxlan", "stp")


async def test_node_capabilities_default_null(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlNodeRepository(sessionmaker)
    await repo.save(
        Node(
            id=NodeId("node_1"),
            name="edge-1",
            mgmt_ip="10.0.0.10",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )

    fetched = await repo.get(NodeId("node_1"))

    assert fetched is not None
    assert fetched.capabilities is None


async def test_enrollment_token_round_trip(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    nodes = SqlNodeRepository(sessionmaker)
    tokens = SqlEnrollmentTokenRepository(sessionmaker)
    await nodes.save(
        Node(
            id=NodeId("node_1"),
            name="edge-1",
            mgmt_ip="10.0.0.10",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    token = EnrollmentToken.issue(
        token_id=EnrollmentTokenId("enroll_1"),
        node_id=NodeId("node_1"),
        plaintext="some-secret",
        now=_NOW,
        ttl=timedelta(hours=1),
        issued_by="alice",
    )

    await tokens.save(token)
    fetched_by_id = await tokens.get(EnrollmentTokenId("enroll_1"))
    fetched_by_hash = await tokens.get_by_hash(token.token_hash)

    assert fetched_by_id is not None
    assert fetched_by_hash is not None
    assert fetched_by_id.token_hash == fetched_by_hash.token_hash
    assert fetched_by_id.issued_by == "alice"


async def test_delete_node_cascades_to_enrollment_tokens(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    nodes = SqlNodeRepository(sessionmaker)
    tokens = SqlEnrollmentTokenRepository(sessionmaker)
    await nodes.save(
        Node(
            id=NodeId("node_1"),
            name="edge-1",
            mgmt_ip="10.0.0.10",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    token = EnrollmentToken.issue(
        token_id=EnrollmentTokenId("enroll_1"),
        node_id=NodeId("node_1"),
        plaintext="x",
        now=_NOW,
        ttl=timedelta(hours=1),
    )
    await tokens.save(token)

    await nodes.delete(NodeId("node_1"))

    # FK ON DELETE CASCADE removed the dependent row.
    assert await tokens.get(EnrollmentTokenId("enroll_1")) is None


async def test_list_for_node_orders_most_recent_first(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    nodes = SqlNodeRepository(sessionmaker)
    tokens = SqlEnrollmentTokenRepository(sessionmaker)
    await nodes.save(
        Node(
            id=NodeId("node_1"),
            name="edge-1",
            mgmt_ip="10.0.0.10",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    for i in range(3):
        await tokens.save(
            EnrollmentToken.issue(
                token_id=EnrollmentTokenId(f"enroll_{i}"),
                node_id=NodeId("node_1"),
                plaintext=f"secret-{i}",
                now=_NOW + timedelta(seconds=i),
                ttl=timedelta(hours=1),
            )
        )

    listed = await tokens.list_for_node(NodeId("node_1"))

    assert [t.id for t in listed] == ["enroll_2", "enroll_1", "enroll_0"]
