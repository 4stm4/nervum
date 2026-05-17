"""``Snapshot`` and ``Restore`` use cases.

Snapshots wrap a dump of OVS state together with the state's content hash
(so a restore can re-verify integrity) and a free-form label set by the
caller (e.g. ``before plan plan_01HX``).
"""

from __future__ import annotations

from netos_agent.core.entities import OvsSnapshot
from netos_agent.core.services.clock import Clock
from netos_agent.core.value_objects.errors import NotFoundError
from netos_agent.core.value_objects.ids import IdFactory, SnapshotId
from netos_agent.ports.ovsdb import OvsdbPort
from netos_agent.ports.snapshots import SnapshotRepository


class Snapshot:
    def __init__(
        self,
        *,
        ovsdb: OvsdbPort,
        snapshots: SnapshotRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._ovsdb = ovsdb
        self._snapshots = snapshots
        self._clock = clock
        self._ids = ids

    async def execute(self, *, label: str | None = None) -> OvsSnapshot:
        state = await self._ovsdb.get_state()
        payload = await self._ovsdb.dump()
        snapshot = OvsSnapshot(
            id=self._ids.snapshot(),
            created_at=self._clock.now(),
            state_hash=state.hash,
            payload=payload,
            label=label,
        )
        await self._snapshots.save(snapshot)
        return snapshot


class Restore:
    def __init__(
        self,
        *,
        ovsdb: OvsdbPort,
        snapshots: SnapshotRepository,
    ) -> None:
        self._ovsdb = ovsdb
        self._snapshots = snapshots

    async def execute(self, snapshot_id: SnapshotId) -> OvsSnapshot:
        snapshot = await self._snapshots.get(snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"snapshot {snapshot_id} not found")
        await self._ovsdb.restore(snapshot.payload)
        return snapshot


class ListSnapshots:
    def __init__(self, *, snapshots: SnapshotRepository) -> None:
        self._snapshots = snapshots

    async def execute(self) -> list[OvsSnapshot]:
        return await self._snapshots.list()
