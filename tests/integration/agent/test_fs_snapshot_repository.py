"""Filesystem-backed snapshot store round-trips and ordering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from netos_agent.adapters.snapshots_fs import FsSnapshotRepository
from netos_agent.core.entities import OvsSnapshot
from netos_agent.core.value_objects.ids import SnapshotId


def _snap(snap_id: str, at: datetime, *, label: str | None = None) -> OvsSnapshot:
    return OvsSnapshot(
        id=SnapshotId(snap_id),
        created_at=at,
        state_hash="0" * 64,
        payload={"ovs_version": "fake", "bridges": []},
        label=label,
    )


async def test_save_and_get(tmp_path: Path) -> None:
    repo = FsSnapshotRepository(tmp_path)
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    snap = _snap("snap_1", now, label="pre-apply")

    await repo.save(snap)
    fetched = await repo.get(SnapshotId("snap_1"))

    assert fetched is not None
    assert fetched.label == "pre-apply"
    assert fetched.created_at == now
    assert fetched.payload == {"ovs_version": "fake", "bridges": []}


async def test_get_missing_returns_none(tmp_path: Path) -> None:
    repo = FsSnapshotRepository(tmp_path)

    assert await repo.get(SnapshotId("snap_missing")) is None


async def test_list_orders_most_recent_first(tmp_path: Path) -> None:
    repo = FsSnapshotRepository(tmp_path)
    base = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    await repo.save(_snap("snap_a", base))
    await repo.save(_snap("snap_b", base + timedelta(seconds=10)))
    await repo.save(_snap("snap_c", base + timedelta(seconds=5)))

    ids = [s.id for s in await repo.list()]

    assert ids == ["snap_b", "snap_c", "snap_a"]


async def test_delete_removes_file(tmp_path: Path) -> None:
    repo = FsSnapshotRepository(tmp_path)
    await repo.save(_snap("snap_x", datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)))

    await repo.delete(SnapshotId("snap_x"))

    assert await repo.get(SnapshotId("snap_x")) is None
    # Repeating delete must not raise.
    await repo.delete(SnapshotId("snap_x"))
