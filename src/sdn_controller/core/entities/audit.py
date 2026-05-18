"""Immutable audit events (SDN-033).

Audit — это append-only журнал «кто что сделал». Отличается от
``Operation.events`` (шаги одной операции внутри aggregate'а): аудит
живёт глобально, индексируется по actor/action/resource/времени,
никогда не редактируется и не удаляется.

Поля выбраны так, чтобы покрыть основные сценарии:

* ``actor`` — имя service-account'а, инициировавшего действие
  (например, ``ops-ci`` или ``bootstrap-admin``); ``None`` для
  событий, инициированных самой системой (bootstrap, reaper).
* ``action`` — короткий идентификатор операции в формате
  ``<resource>.<verb>`` (``network.create``, ``service_token.issue``).
* ``resource`` — ссылка на объект-цель (тип + id).
* ``http_status`` — итоговый код ответа; помогает отличать удачные
  действия от попыток, отбитых валидацией.
* ``payload`` — небольшой JSON со «второстепенными» подробностями
  (имя сети, vni, label токена). Секреты (plaintext'ы, hash'и) сюда
  **не** попадают по дизайну.
* ``request_id`` — корреляция с серверными логами и middleware'ом.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import AuditEventId


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: AuditEventId
    at: datetime
    action: str
    resource_type: str
    resource_id: str | None = None
    actor: str | None = None
    http_status: int | None = None
    request_id: str | None = None
    payload: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action or "." not in self.action:
            raise ValidationError(
                f"audit action must be in '<resource>.<verb>' form: {self.action!r}",
            )
        if not self.resource_type:
            raise ValidationError("audit resource_type must be non-empty")


__all__ = ["AuditEvent"]
