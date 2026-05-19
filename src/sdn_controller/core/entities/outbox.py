"""Transactional outbox event (SDN-055).

Outbox — append-only журнал событий контроллера, который потом
дочитывает webhook-dispatcher (SDN-054) и snapshot-consumer (SDN-057).

Ключевые свойства:

* ``event_id`` — монотонно возрастающий int, **независим от** ``id``;
  именно его подписчик использует как watermark («дай мне всё, что
  больше X»). На уровне entity он не присваивается — это делает
  адаптер при `append()` (SQL — autoincrement, in-memory — счётчик).
  В моменте до записи ``event_id`` равен 0.
* ``event_type`` — короткий идентификатор события в формате
  ``<resource>.<verb>`` (``network.created``, ``node.enrolled``,
  ``network.applied``).
* ``payload`` — JSON-структура события. Контракт — стабильный, мы его
  фиксируем в ``docs/integrations/testum.md``. Sensitive-данные
  (plaintext-токены, hash'и) сюда **не** попадают по дизайну.
* ``delivered_at`` — выставляется dispatcher'ом, когда все активные
  подписки приняли событие; используется retention-job'ом, чтобы
  удалять старые delivered-события.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import OutboxEventId


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: OutboxEventId
    event_id: int  # monotonic; assigned by the adapter at ``append`` time
    occurred_at: datetime
    event_type: str
    resource_type: str
    resource_id: str | None = None
    payload: dict[str, object] = field(default_factory=dict)
    delivered_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.event_type or "." not in self.event_type:
            raise ValidationError(
                f"outbox event_type must be '<resource>.<verb>': {self.event_type!r}",
            )
        if not self.resource_type:
            raise ValidationError("outbox resource_type must be non-empty")
        if self.event_id < 0:
            raise ValidationError("outbox event_id must be >= 0")


__all__ = ["OutboxEvent"]
