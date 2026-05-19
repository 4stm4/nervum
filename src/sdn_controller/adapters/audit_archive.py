"""Реализации ``AuditArchive`` (SDN-040)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio

from sdn_controller.core.entities import AuditEvent


class NoopAuditArchive:
    """Сжигает события — используется, когда оператор настроил
    retention без отдельного архива (всё уже в SIEM/logging-stack)."""

    async def archive(self, events: Sequence[AuditEvent]) -> None:
        return None


class FileAuditArchive:
    """JSON-line append в каталог. Файлы делятся по дате события:
    ``audit-2026-05-19.jsonl``. После записи в текущий день append'ится,
    переход на следующий день создаёт новый файл сам.

    Файлы пишутся атомарно по строке (`open + write + close`) — это
    дёшево и не требует rotation-logic'а.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._lock = anyio.Lock()

    async def archive(self, events: Sequence[AuditEvent]) -> None:
        if not events:
            return None
        await asyncio.to_thread(self._dir.mkdir, exist_ok=True, parents=True)
        # Группируем по дате для дешёвого вычисления target-файла.
        bucketed: dict[str, list[AuditEvent]] = {}
        for ev in events:
            bucketed.setdefault(_bucket(ev.at), []).append(ev)
        async with self._lock:
            for bucket, items in bucketed.items():
                path = self._dir / f"audit-{bucket}.jsonl"
                payload = "\n".join(json.dumps(_to_json(e), default=str) for e in items) + "\n"
                await asyncio.to_thread(_append_file, path, payload)
        return None


def _bucket(at: datetime) -> str:
    return at.strftime("%Y-%m-%d")


def _append_file(path: Path, payload: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(payload)


def _to_json(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "at": event.at.isoformat(),
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "actor": event.actor,
        "http_status": event.http_status,
        "request_id": event.request_id,
        "payload": dict(event.payload),
    }


__all__ = ["FileAuditArchive", "NoopAuditArchive"]
