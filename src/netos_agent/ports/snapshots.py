"""Snapshot storage port — keep persisted dumps separate from live OVS state."""

from __future__ import annotations

from typing import Protocol

from netos_agent.core.entities import OvsSnapshot
from netos_agent.core.value_objects.ids import SnapshotId


class SnapshotRepository(Protocol):
    async def save(self, snapshot: OvsSnapshot) -> None: ...
    async def get(self, snapshot_id: SnapshotId) -> OvsSnapshot | None: ...
    async def list(self) -> list[OvsSnapshot]: ...
    async def delete(self, snapshot_id: SnapshotId) -> None: ...
