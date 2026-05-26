"""Сущность ProjectQuota — квоты ресурсов проекта (N4-01).

Хранит лимиты по типам ресурсов (сети, маршрутизаторы, FIP, порты и т.д.).
``None`` означает «без ограничений». Обслуживает QuotaService.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.enums import QuotaResource
from sdn_controller.core.value_objects.ids import ProjectId, ProjectQuotaId


@dataclass
class ProjectQuota:
    """Квоты ресурсов для одного проекта (N4-01).

    ``limits`` — словарь ``QuotaResource → int | None``.
    ``None`` означает неограниченное количество.
    Отсутствие ключа эквивалентно ``None``.
    """

    id: ProjectQuotaId
    project_id: ProjectId
    limits: dict[str, int | None] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def get_limit(self, resource: QuotaResource) -> int | None:
        """Возвращает лимит для ресурса (None — без ограничений)."""
        return self.limits.get(resource.value)

    def set_limit(self, resource: QuotaResource, value: int | None, *, now: datetime) -> None:
        """Устанавливает лимит для ресурса. None снимает ограничение."""
        if value is not None and value < 0:
            from sdn_controller.core.value_objects.errors import ValidationError
            raise ValidationError(f"лимит должен быть >= 0, получено {value}")
        self.limits[resource.value] = value
        self.updated_at = now

    def remove_limit(self, resource: QuotaResource, *, now: datetime) -> None:
        """Удаляет лимит (снимает ограничение)."""
        self.limits.pop(resource.value, None)
        self.updated_at = now
