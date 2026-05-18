"""Unit-тесты для TakeNodeSnapshot / RestoreNodeSnapshot через FakeAgent."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdn_controller.adapters.memory import (
    InMemoryNodeRepository,
    InMemoryNodeSnapshotRepository,
)
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.core.entities import Node
from sdn_controller.core.use_cases.node_snapshots import (
    ListNodeSnapshots,
    RestoreNodeSnapshot,
    TakeNodeSnapshot,
    TakeSnapshotCommand,
)
from sdn_controller.core.value_objects.enums import NodeStatus
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import NodeId, NodeSnapshotId
from sdn_controller.ports.agent import EnsureBridgeStep, Plan
from tests.conftest import CountingIdFactory, FrozenClock

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)


def _node(node_id: str) -> Node:
    return Node(
        id=NodeId(node_id),
        name=node_id,
        mgmt_ip="10.0.0.1",
        status=NodeStatus.ONLINE,
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
def nodes() -> InMemoryNodeRepository:
    return InMemoryNodeRepository()


@pytest.fixture
def snapshots() -> InMemoryNodeSnapshotRepository:
    return InMemoryNodeSnapshotRepository()


@pytest.fixture
def take(
    nodes: InMemoryNodeRepository,
    snapshots: InMemoryNodeSnapshotRepository,
    fake_agent: FakeAgent,
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> TakeNodeSnapshot:
    return TakeNodeSnapshot(
        nodes=nodes,
        snapshots=snapshots,
        agent=fake_agent,
        clock=clock,
        ids=ids,
    )


async def test_take_creates_local_catalog_entry(
    nodes: InMemoryNodeRepository,
    snapshots: InMemoryNodeSnapshotRepository,
    take: TakeNodeSnapshot,
) -> None:
    node = _node("node_1")
    await nodes.save(node)

    snap = await take.execute(TakeSnapshotCommand(node_id=node.id, label="pre-upgrade"))
    assert snap.node_id == node.id
    assert snap.label == "pre-upgrade"
    assert snap.agent_snapshot_id
    assert snap.state_hash

    stored = await snapshots.list_for_node(node.id)
    assert [s.id for s in stored] == [snap.id]


async def test_take_for_unknown_node_raises(take: TakeNodeSnapshot) -> None:
    with pytest.raises(NotFoundError):
        await take.execute(TakeSnapshotCommand(node_id=NodeId("missing")))


async def test_list_for_unknown_node_raises(
    nodes: InMemoryNodeRepository,
    snapshots: InMemoryNodeSnapshotRepository,
) -> None:
    list_uc = ListNodeSnapshots(nodes=nodes, snapshots=snapshots)
    with pytest.raises(NotFoundError):
        await list_uc.execute(NodeId("missing"))


async def test_restore_calls_agent_with_agent_snapshot_id(
    nodes: InMemoryNodeRepository,
    snapshots: InMemoryNodeSnapshotRepository,
    take: TakeNodeSnapshot,
    fake_agent: FakeAgent,
) -> None:
    node = _node("node_1")
    await nodes.save(node)

    # 1) Возьмём снапшот пустого состояния.
    snap = await take.execute(TakeSnapshotCommand(node_id=node.id))

    # 2) Сменим OVS-state на агенте (добавим мост).
    await fake_agent.apply_plan(
        node.id,
        Plan(plan_id="drift", steps=(EnsureBridgeStep(name="br-other"),)),
    )
    assert (await fake_agent.get_state(node.id)).find_bridge("br-other") is not None

    # 3) Восстановимся — мост исчезает.
    restore = RestoreNodeSnapshot(snapshots=snapshots, agent=fake_agent)
    result = await restore.execute(snap.id)
    assert result.snapshot.id == snap.id
    assert (await fake_agent.get_state(node.id)).find_bridge("br-other") is None


async def test_restore_unknown_snapshot_raises(
    snapshots: InMemoryNodeSnapshotRepository,
    fake_agent: FakeAgent,
) -> None:
    restore = RestoreNodeSnapshot(snapshots=snapshots, agent=fake_agent)
    with pytest.raises(NotFoundError):
        await restore.execute(NodeSnapshotId("missing"))
