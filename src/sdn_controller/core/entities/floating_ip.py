"""Сущность FloatingIP — плавающий публичный IP (N3-02).

Жизненный цикл:
  allocate  → FloatingIP.status = DOWN (выделен, не ассоциирован)
  associate → FloatingIP.status = ACTIVE (ассоциирован с logical_port)
  dissociate→ FloatingIP.status = DOWN
  release   → запись удаляется

``floating_ip_address`` — публичный IP во внешней сети.
``fixed_ip_address``    — приватный IP порта (заполняется при ассоциации).
``router_id``           — маршрутизатор, через который выполняется DNAT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_address

from sdn_controller.core.value_objects.enums import FloatingIpStatus
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import (
    FloatingIpId,
    LogicalPortId,
    NetworkId,
    ProjectId,
    RouterId,
)


@dataclass
class FloatingIP:
    """Floating IP — публичный адрес с DNAT на приватный порт (N3-02)."""

    id: FloatingIpId
    external_network_id: NetworkId
    floating_ip_address: str
    project_id: ProjectId | None = None
    fixed_ip_address: str | None = None
    logical_port_id: LogicalPortId | None = None
    router_id: RouterId | None = None
    status: FloatingIpStatus = FloatingIpStatus.DOWN
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        try:
            ip_address(self.floating_ip_address)
        except ValueError:
            raise ValidationError(
                f"некорректный floating_ip_address: {self.floating_ip_address!r}"
            )
        if self.fixed_ip_address is not None:
            try:
                ip_address(self.fixed_ip_address)
            except ValueError:
                raise ValidationError(
                    f"некорректный fixed_ip_address: {self.fixed_ip_address!r}"
                )

    def associate(
        self,
        *,
        logical_port_id: LogicalPortId,
        fixed_ip_address: str,
        router_id: RouterId,
        now: datetime,
    ) -> None:
        """Ассоциирует Floating IP с логическим портом через маршрутизатор."""
        try:
            ip_address(fixed_ip_address)
        except ValueError:
            raise ValidationError(f"некорректный fixed_ip_address: {fixed_ip_address!r}")
        self.logical_port_id = logical_port_id
        self.fixed_ip_address = fixed_ip_address
        self.router_id = router_id
        self.status = FloatingIpStatus.ACTIVE
        self.updated_at = now

    def disassociate(self, *, now: datetime) -> None:
        """Снимает ассоциацию; FIP переходит в DOWN."""
        self.logical_port_id = None
        self.fixed_ip_address = None
        self.router_id = None
        self.status = FloatingIpStatus.DOWN
        self.updated_at = now

    def update_labels(self, labels: dict[str, str], *, now: datetime) -> None:
        self.labels = labels
        self.updated_at = now
