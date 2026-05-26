"""Сущность MirrorSession — зеркалирование трафика порта (N5-02).

Port mirroring (SPAN / ERSPAN) перехватывает трафик на source-порту
и перенаправляет копию на destination. Используется для мониторинга,
IDS/IPS, дебаггинга.

``destination_port_id`` — локальный порт назначения (SPAN).
``destination_ip``      — удалённый IP (ERSPAN) — взаимоисключающе с port_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.enums import MirrorDirection, MirrorStatus
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import (
    LogicalPortId,
    MirrorSessionId,
    ProjectId,
)


@dataclass
class MirrorSession:
    """SPAN / ERSPAN mirror-сессия (N5-02).

    ``source_port_id``      — порт, с которого снимается трафик.
    ``destination_port_id`` — локальный порт назначения (SPAN).
    ``destination_ip``      — IP-адрес ERSPAN-коллектора (вместо порта).
    ``direction``           — ingress / egress / both.
    ``filter_vlan``         — необязательный VLAN-фильтр (1–4094).
    ``applied_config``      — последний сгенерированный конфиг OVS.
    """

    id: MirrorSessionId
    name: str
    source_port_id: LogicalPortId
    direction: MirrorDirection
    destination_port_id: LogicalPortId | None = None
    destination_ip: str | None = None
    filter_vlan: int | None = None
    project_id: ProjectId | None = None
    status: MirrorStatus = MirrorStatus.INACTIVE
    applied_config: str | None = None
    applied_at: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now())
    updated_at: datetime = field(default_factory=lambda: datetime.now())

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if self.destination_port_id is None and self.destination_ip is None:
            raise ValidationError(
                "mirror_session: требуется destination_port_id или destination_ip"
            )
        if self.destination_port_id is not None and self.destination_ip is not None:
            raise ValidationError(
                "mirror_session: нельзя задать destination_port_id и destination_ip одновременно"
            )
        if self.filter_vlan is not None and not (1 <= self.filter_vlan <= 4094):
            raise ValidationError(
                f"filter_vlan должен быть в диапазоне 1–4094, получено {self.filter_vlan}"
            )

    def apply(self, config: str, now: datetime) -> None:
        """Сохранить применённый конфиг и пометить активной."""
        self.applied_config = config
        self.applied_at = now
        self.status = MirrorStatus.ACTIVE
        self.updated_at = now
