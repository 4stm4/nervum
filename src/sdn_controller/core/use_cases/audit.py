"""Use case'ы аудита (SDN-033).

Очень тонкие: ``RecordAudit`` пишет один эвент, ``ListAuditEvents``
читает срез с фильтрами. Изоляция в use case'е нужна, чтобы:

* middleware не знал о репозитории напрямую;
* фильтры жили в одном месте — обычной валидацией Pydantic'а в
  endpoint'е это не покрыть, лимиты сидят здесь.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.entities import AuditEvent
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.value_objects.ids import IdFactory
from sdn_controller.ports.persistence import AuditEventRepository

_DEFAULT_LIST_LIMIT = 100
_MAX_LIST_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class RecordAuditCommand:
    action: str
    resource_type: str
    resource_id: str | None = None
    actor: str | None = None
    http_status: int | None = None
    request_id: str | None = None
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ListAuditEventsCommand:
    actor: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    since: datetime | None = None
    limit: int = _DEFAULT_LIST_LIMIT


class RecordAudit:
    def __init__(
        self,
        *,
        audit_events: AuditEventRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._repo = audit_events
        self._clock = clock
        self._ids = ids

    async def execute(self, cmd: RecordAuditCommand) -> AuditEvent:
        event = AuditEvent(
            id=self._ids.audit_event(),
            at=self._clock.now(),
            action=cmd.action,
            resource_type=cmd.resource_type,
            resource_id=cmd.resource_id,
            actor=cmd.actor,
            http_status=cmd.http_status,
            request_id=cmd.request_id,
            payload=dict(cmd.payload),
        )
        await self._repo.save(event)
        return event


class ListAuditEvents:
    def __init__(self, *, audit_events: AuditEventRepository) -> None:
        self._repo = audit_events

    async def execute(self, cmd: ListAuditEventsCommand) -> list[AuditEvent]:
        limit = min(max(1, cmd.limit), _MAX_LIST_LIMIT)
        return await self._repo.list(
            actor=cmd.actor,
            action=cmd.action,
            resource_type=cmd.resource_type,
            resource_id=cmd.resource_id,
            since=cmd.since,
            limit=limit,
        )


__all__ = [
    "ListAuditEvents",
    "ListAuditEventsCommand",
    "RecordAudit",
    "RecordAuditCommand",
]
