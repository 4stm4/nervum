"""Сущность HealthMonitor — мониторинг здоровья бэкендов LB (N4-07).

Один пул (LbPool) — один HealthMonitor. Конфигуратор LB включает
health-check-блок на основе этой сущности. В MVP реального зондирования
нет — конфиг генерируется и сохраняется (агент применяет его в N5+).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.enums import HealthCheckType
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import HealthMonitorId, LbPoolId

_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass
class HealthMonitor:
    """Конфигурация health-check для пула балансировщика (N4-07).

    ``delay``       — интервал между проверками (секунды, >= 1).
    ``timeout``     — таймаут одной проверки (секунды, >= 1).
    ``max_retries`` — число последовательных неудач до пометки DOWN (1–10).
    ``url_path``    — путь HTTP/HTTPS проверки (``/health`` по умолчанию).
    ``http_method`` — метод HTTP-проверки (GET, HEAD).
    ``expected_codes`` — ожидаемые коды ответа (``200``, ``200-299`` и т.п.).
    """

    id: HealthMonitorId
    pool_id: LbPoolId
    check_type: HealthCheckType
    delay: int = 5
    timeout: int = 3
    max_retries: int = 3
    url_path: str = "/health"
    http_method: str = "GET"
    expected_codes: str = "200"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if self.delay < 1:
            raise ValidationError(f"delay должен быть >= 1, получено {self.delay}")
        if self.timeout < 1:
            raise ValidationError(f"timeout должен быть >= 1, получено {self.timeout}")
        if not (1 <= self.max_retries <= 10):
            raise ValidationError(
                f"max_retries должен быть 1–10, получено {self.max_retries}"
            )
        if self.http_method not in _METHODS:
            raise ValidationError(
                f"http_method должен быть одним из {sorted(_METHODS)}, "
                f"получено {self.http_method!r}"
            )

    def update(
        self,
        *,
        delay: int | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
        url_path: str | None = None,
        http_method: str | None = None,
        expected_codes: str | None = None,
        now: datetime,
    ) -> None:
        if delay is not None:
            self.delay = delay
        if timeout is not None:
            self.timeout = timeout
        if max_retries is not None:
            self.max_retries = max_retries
        if url_path is not None:
            self.url_path = url_path
        if http_method is not None:
            self.http_method = http_method
        if expected_codes is not None:
            self.expected_codes = expected_codes
        self._validate()
        self.updated_at = now
