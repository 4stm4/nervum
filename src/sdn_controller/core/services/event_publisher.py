"""``EventPublisher`` — fasade для записи в outbox (SDN-055).

Каждый mutating use case вызывает ``publisher.publish(...)`` сразу
после ``repo.save()``. Внутри собирается ``OutboxEvent``, ему
присваивается монотонный ``event_id`` (через адаптер), и он сохраняется
в outbox.

Логически это «outbox в той же операции» — но физически в текущем M13
запись идёт **двумя последовательными commit'ами** (доменный + outbox).
При краше между ними событие потеряется. Это at-most-once в очень
редком крае; для MVP допустимо. Полноценный transactional outbox с
UnitOfWork — отдельная задача (M15+).
"""

from __future__ import annotations

from sdn_controller.core.entities import OutboxEvent
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.value_objects.ids import IdFactory, OutboxEventId
from sdn_controller.ports.persistence import OutboxRepository


class EventPublisher:
    def __init__(
        self,
        *,
        outbox: OutboxRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._outbox = outbox
        self._clock = clock
        self._ids = ids

    async def publish(
        self,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        payload: dict[str, object] | None = None,
        project_id: str | None = None,
    ) -> OutboxEvent:
        event = OutboxEvent(
            id=OutboxEventId(self._ids.outbox_event()),
            event_id=0,  # будет присвоен адаптером
            occurred_at=self._clock.now(),
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=dict(payload or {}),
            schema_version=2,
            project_id=project_id,
        )
        return await self._outbox.append(event)


__all__ = ["EventPublisher"]
