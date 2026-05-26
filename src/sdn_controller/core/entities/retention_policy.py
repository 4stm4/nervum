"""Сущность RetentionPolicy — политика хранения данных (N4-05).

Позволяет задавать время хранения для разных типов ресурсов
как глобально, так и на уровне проекта.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.enums import RetentionScope
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import ProjectId, RetentionPolicyId

_MIN_DAYS = 1
_MAX_DAYS = 36500  # 100 лет


@dataclass
class RetentionPolicy:
    """Политика хранения для одного типа ресурсов (N4-05).

    ``project_id = None`` означает глобальную политику (применяется ко всем
    проектам, у которых нет собственной политики того же типа).
    ``retention_days`` — сколько дней хранить данные; 0 = хранить вечно.
    """

    id: RetentionPolicyId
    scope: RetentionScope
    retention_days: int
    project_id: ProjectId | None = None
    description: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        self._validate_days(self.retention_days)

    def _validate_days(self, days: int) -> None:
        if days != 0 and not (_MIN_DAYS <= days <= _MAX_DAYS):
            raise ValidationError(
                f"retention_days должно быть 0 (вечно) или от {_MIN_DAYS} до {_MAX_DAYS}, "
                f"получено {days}"
            )

    def update(self, *, retention_days: int, description: str | None, now: datetime) -> None:
        """Обновляет количество дней хранения."""
        self._validate_days(retention_days)
        self.retention_days = retention_days
        if description is not None:
            self.description = description
        self.updated_at = now
