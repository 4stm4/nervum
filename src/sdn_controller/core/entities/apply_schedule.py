"""Сущность ApplySchedule — cron-расписание автоматического apply (N5-01).

Позволяет оператору задать расписание в стандартном cron-формате
(5 полей: минута час день месяц день_недели), по которому контроллер
автоматически вызывает apply для указанного ресурса.

``target_type`` + ``target_id`` однозначно идентифицируют ресурс.
``last_run_at`` / ``last_run_status`` хранят результат последнего запуска.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.enums import ScheduleStatus, ScheduleTargetType
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import ApplyScheduleId, ProjectId


@dataclass
class ApplySchedule:
    """Cron-расписание автоматического apply (N5-01).

    ``cron_expr``        — стандартное 5-польное выражение (``* * * * *``).
    ``target_type``      — тип ресурса (network, router, load_balancer, gateway_bond).
    ``target_id``        — ID ресурса в виде строки.
    ``enabled``          — активно ли расписание.
    ``last_run_at``      — момент последнего запуска (None если не запускалось).
    ``last_run_status``  — «ok» или «error» с описанием последней ошибки.
    """

    id: ApplyScheduleId
    name: str
    cron_expr: str
    target_type: ScheduleTargetType
    target_id: str
    enabled: bool = True
    project_id: ProjectId | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now())
    updated_at: datetime = field(default_factory=lambda: datetime.now())

    def __post_init__(self) -> None:
        self._validate_cron()

    def _validate_cron(self) -> None:
        """Базовая синтаксическая проверка cron-выражения."""
        parts = self.cron_expr.strip().split()
        if len(parts) != 5:
            raise ValidationError(
                f"cron_expr должен содержать 5 полей, получено {len(parts)}: "
                f"{self.cron_expr!r}"
            )

    def enable(self, now: datetime) -> None:
        """Включить расписание."""
        self.enabled = True
        self.status = ScheduleStatus.ACTIVE
        self.updated_at = now

    def pause(self, now: datetime) -> None:
        """Приостановить расписание без удаления."""
        self.enabled = False
        self.status = ScheduleStatus.PAUSED
        self.updated_at = now

    def record_run(self, *, success: bool, error_msg: str | None, now: datetime) -> None:
        """Обновить статистику последнего запуска."""
        self.last_run_at = now
        if success:
            self.last_run_status = "ok"
            self.status = ScheduleStatus.ACTIVE
        else:
            self.last_run_status = f"error: {error_msg or 'unknown'}"
            self.status = ScheduleStatus.ERROR
        self.updated_at = now
