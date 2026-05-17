"""On-disk snapshot store.

Each snapshot becomes a single JSON file named ``<id>.json`` inside the
configured directory. Why JSON files and not SQLite?

* snapshots are infrequent (one per risky apply, plus operator-triggered
  ones) — there's no query workload that benefits from a database;
* file-per-snapshot makes ``ls``/``rm`` admin operations trivial;
* a future move to object storage (S3, etc.) becomes "same adapter, different
  driver" rather than a schema migration.

We use ``anyio.to_thread.run_sync`` to keep filesystem syscalls off the event
loop — small files but the bytes still hit a syscall.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import anyio

from netos_agent.core.entities import OvsSnapshot
from netos_agent.core.value_objects.errors import ValidationError
from netos_agent.core.value_objects.ids import SnapshotId


class FsSnapshotRepository:
    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    async def save(self, snapshot: OvsSnapshot) -> None:
        path = self._path_for(snapshot.id)
        payload: dict[str, Any] = {
            "id": snapshot.id,
            "created_at": snapshot.created_at.isoformat(),
            "state_hash": snapshot.state_hash,
            "payload": snapshot.payload,
            "label": snapshot.label,
        }
        await anyio.to_thread.run_sync(_write_json, path, payload)

    async def get(self, snapshot_id: SnapshotId) -> OvsSnapshot | None:
        path = self._path_for(snapshot_id)
        if not path.exists():
            return None
        payload = await anyio.to_thread.run_sync(_read_json, path)
        return _from_payload(payload)

    async def list(self) -> list[OvsSnapshot]:
        paths = sorted(self._dir.glob("*.json"))

        def _load_all() -> list[OvsSnapshot]:
            return [_from_payload(_read_json(p)) for p in paths]

        snaps = await anyio.to_thread.run_sync(_load_all)
        snaps.sort(key=lambda s: s.created_at, reverse=True)
        return snaps

    async def delete(self, snapshot_id: SnapshotId) -> None:
        path = self._path_for(snapshot_id)
        await anyio.to_thread.run_sync(_unlink_if_exists, path)

    def _path_for(self, snapshot_id: SnapshotId) -> Path:
        return self._dir / f"{snapshot_id}.json"


# ---------------------------------------------------------------------------
# Module-level helpers (kept out of the class so they're trivially testable)
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


def _unlink_if_exists(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _from_payload(payload: dict[str, Any]) -> OvsSnapshot:
    try:
        created_at = datetime.fromisoformat(payload["created_at"])
    except (KeyError, ValueError) as exc:
        raise ValidationError(f"snapshot file is missing/invalid created_at: {exc}") from exc
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return OvsSnapshot(
        id=SnapshotId(str(payload["id"])),
        created_at=created_at,
        state_hash=str(payload["state_hash"]),
        payload=dict(payload.get("payload") or {}),
        label=payload.get("label"),
    )
