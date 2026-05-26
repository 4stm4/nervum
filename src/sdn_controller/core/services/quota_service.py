"""QuotaService — проверка квот ресурсов проекта (N4-01).

Сервис читает ProjectQuota из репозитория и сопоставляет текущее
использование с лимитами. Вызывается перед созданием ресурсов в use-cases.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdn_controller.core.value_objects.enums import QuotaResource
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import ProjectId


@dataclass(frozen=True)
class QuotaViolation:
    """Информация об одном превышении квоты."""

    resource: QuotaResource
    limit: int
    current: int


class QuotaService:
    """Проверяет и соблюдает квоты ресурсов (N4-01).

    Используется в use cases перед созданием нового ресурса.
    Если квота не задана для проекта, ресурс не ограничен.
    """

    async def check(
        self,
        project_id: ProjectId | None,
        resource: QuotaResource,
        current_count: int,
        quota_repo: object,
    ) -> None:
        """Проверяет, не превышает ли ``current_count + 1`` лимит.

        Поднимает ``ValidationError`` при превышении.
        ``quota_repo`` должен реализовывать ``get_by_project(project_id)``.
        """
        if project_id is None:
            return
        quota = await quota_repo.get_by_project(project_id)  # type: ignore[attr-defined]
        if quota is None:
            return
        limit = quota.get_limit(resource)
        if limit is None:
            return
        if current_count >= limit:
            raise ValidationError(
                f"превышена квота для {resource.value}: лимит {limit}, "
                f"текущее использование {current_count}"
            )

    def compute_violations(
        self,
        quota: object,
        usage: dict[str, int],
    ) -> list[QuotaViolation]:
        """Вычисляет список нарушений квоты без поднятия исключения.

        ``quota`` — объект ProjectQuota.
        ``usage`` — словарь ``resource_type → current_count``.
        """
        violations: list[QuotaViolation] = []
        for resource in QuotaResource:
            limit = quota.get_limit(resource)  # type: ignore[attr-defined]
            if limit is None:
                continue
            current = usage.get(resource.value, 0)
            if current > limit:
                violations.append(QuotaViolation(
                    resource=resource,
                    limit=limit,
                    current=current,
                ))
        return violations
