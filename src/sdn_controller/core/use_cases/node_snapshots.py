"""Use cases каталога снапшотов узлов (SDN-035).

Поток:

* ``TakeNodeSnapshot`` зовёт ``agent.snapshot()`` — агент сам сохранит
  байты у себя — и пишет ссылку в наш ``NodeSnapshotRepository``.
* ``ListNodeSnapshots`` / ``GetNodeSnapshot`` отдают каталог.
* ``RestoreNodeSnapshot`` зовёт ``agent.restore(agent_snapshot_id)``;
  локальная запись остаётся неизменной — она же ссылка, а не байты.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdn_controller.core.entities import NodeSnapshot
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import (
    IdFactory,
    NodeId,
    NodeSnapshotId,
)
from sdn_controller.ports.agent import AgentPort
from sdn_controller.ports.persistence import (
    NodeRepository,
    NodeSnapshotRepository,
)


@dataclass(frozen=True, slots=True)
class TakeSnapshotCommand:
    node_id: NodeId
    label: str | None = None


class TakeNodeSnapshot:
    def __init__(
        self,
        *,
        nodes: NodeRepository,
        snapshots: NodeSnapshotRepository,
        agent: AgentPort,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._nodes = nodes
        self._snapshots = snapshots
        self._agent = agent
        self._clock = clock
        self._ids = ids

    async def execute(self, cmd: TakeSnapshotCommand) -> NodeSnapshot:
        if await self._nodes.get(cmd.node_id) is None:
            raise NotFoundError(f"node {cmd.node_id} not found")
        ref = await self._agent.snapshot(cmd.node_id, label=cmd.label)
        snapshot = NodeSnapshot(
            id=self._ids.node_snapshot(),
            node_id=cmd.node_id,
            agent_snapshot_id=ref.id,
            state_hash=ref.state_hash,
            created_at=self._clock.now(),
            label=cmd.label,
        )
        await self._snapshots.save(snapshot)
        return snapshot


class ListNodeSnapshots:
    def __init__(
        self,
        *,
        nodes: NodeRepository,
        snapshots: NodeSnapshotRepository,
    ) -> None:
        self._nodes = nodes
        self._snapshots = snapshots

    async def execute(self, node_id: NodeId) -> list[NodeSnapshot]:
        if await self._nodes.get(node_id) is None:
            raise NotFoundError(f"node {node_id} not found")
        return await self._snapshots.list_for_node(node_id)


class GetNodeSnapshot:
    def __init__(self, *, snapshots: NodeSnapshotRepository) -> None:
        self._snapshots = snapshots

    async def execute(self, snapshot_id: NodeSnapshotId) -> NodeSnapshot:
        snap = await self._snapshots.get(snapshot_id)
        if snap is None:
            raise NotFoundError(f"node snapshot {snapshot_id} not found")
        return snap


@dataclass(frozen=True, slots=True)
class RestoreSnapshotResult:
    snapshot: NodeSnapshot


class RestoreNodeSnapshot:
    """Восстановить состояние узла из ранее сделанного снапшота."""

    def __init__(
        self,
        *,
        snapshots: NodeSnapshotRepository,
        agent: AgentPort,
    ) -> None:
        self._snapshots = snapshots
        self._agent = agent

    async def execute(self, snapshot_id: NodeSnapshotId) -> RestoreSnapshotResult:
        snapshot = await self._snapshots.get(snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"node snapshot {snapshot_id} not found")
        await self._agent.restore(snapshot.node_id, snapshot.agent_snapshot_id)
        return RestoreSnapshotResult(snapshot=snapshot)


__all__ = [
    "GetNodeSnapshot",
    "ListNodeSnapshots",
    "RestoreNodeSnapshot",
    "RestoreSnapshotResult",
    "TakeNodeSnapshot",
    "TakeSnapshotCommand",
]
