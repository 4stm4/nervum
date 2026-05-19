"""Порт для архивирования audit-событий перед их удалением (SDN-040)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sdn_controller.core.entities import AuditEvent


class AuditArchive(Protocol):
    """Куда уходят audit-события, прежде чем retention-job их удаляет.

    Реализации:
    * ``NoopAuditArchive`` — выбрасывает в /dev/null (для dev и для
      случаев, когда retention настроен «удалять без архивации»);
    * ``FileAuditArchive`` — JSON-line append в каталог (один файл per
      день), удобно для WORM-storage и downstream-pipeline.

    Будущая S3-реализация с object-lock — отдельный адаптер.
    """

    async def archive(self, events: Sequence[AuditEvent]) -> None: ...
