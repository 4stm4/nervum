"""Сущность GatewayBond — агрегация каналов на шлюзовом узле (N4-04).

Описывает LACP / active-backup bonding-интерфейс на конкретном узле.
BondConfigurator генерирует конфиг netplan/ifupdown по этой сущности.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.enums import BondMode
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import GatewayBondId, NodeId, ProjectId


@dataclass
class GatewayBond:
    """LACP-бонд или active-backup интерфейс на узле (N4-04).

    ``bond_name``  — имя агрегированного интерфейса (bond0, bond1 и т.п.).
    ``members``    — список физических интерфейсов-членов (eth0, eth1 и т.п.).
    ``mtu``        — MTU агрегата (по умолчанию 1500).
    ``applied_config`` — последний сгенерированный конфиг.
    """

    id: GatewayBondId
    name: str
    node_id: NodeId
    bond_name: str
    mode: BondMode = BondMode.NONE
    members: list[str] = field(default_factory=list)
    mtu: int = 1500
    project_id: ProjectId | None = None
    applied_config: str | None = None
    applied_at: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if not self.bond_name:
            raise ValidationError("bond_name не может быть пустым")
        if not (500 <= self.mtu <= 9000):
            raise ValidationError(f"mtu должен быть 500–9000, получено {self.mtu}")

    def mark_applied(self, config: str, *, now: datetime) -> None:
        """Сохраняет применённый конфиг."""
        self.applied_config = config
        self.applied_at = now
        self.updated_at = now

    def update(
        self,
        *,
        name: str | None = None,
        mode: BondMode | None = None,
        members: list[str] | None = None,
        mtu: int | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        if name is not None:
            self.name = name
        if mode is not None:
            self.mode = mode
        if members is not None:
            self.members = list(members)
        if mtu is not None:
            if not (500 <= mtu <= 9000):
                raise ValidationError(f"mtu должен быть 500–9000, получено {mtu}")
            self.mtu = mtu
        if labels is not None:
            self.labels = labels
        self.updated_at = now
