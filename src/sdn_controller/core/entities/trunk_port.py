"""Сущность TrunkPort — транковый порт с поддержкой 802.1q VLAN (N2-05).

TrunkPort привязан к узлу и, опционально, к логическому порту.
Он несёт один или несколько VLAN-тегов и может иметь native VLAN
(нетегированный трафик по умолчанию).

Диапазон допустимых VLAN: 1–4094 (IEEE 802.1q).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import (
    LogicalPortId,
    NodeId,
    ProjectId,
    TrunkPortId,
)

_VLAN_MIN = 1
_VLAN_MAX = 4094

# Sentinel для различения «не передано» и None в update()
_UNSET: Any = object()


def _validate_vlan(vlan: int) -> None:
    if not (_VLAN_MIN <= vlan <= _VLAN_MAX):
        raise ValidationError(
            f"VLAN ID должен быть в диапазоне {_VLAN_MIN}–{_VLAN_MAX}, получено {vlan}"
        )


@dataclass
class TrunkPort:
    """Транковый порт 802.1q на конкретном узле (N2-05).

    Поле ``vlan_ids`` содержит полный список разрешённых VLAN-тегов.
    ``native_vlan`` — нетегированный VLAN (может быть None).
    ``logical_port_id`` — опциональная ссылка на родительский LogicalPort.
    """

    id: TrunkPortId
    name: str
    node_id: NodeId
    vlan_ids: tuple[int, ...]
    logical_port_id: LogicalPortId | None = None
    native_vlan: int | None = None
    project_id: ProjectId | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now())
    updated_at: datetime = field(default_factory=lambda: datetime.now())

    def __post_init__(self) -> None:
        for vlan in self.vlan_ids:
            _validate_vlan(vlan)
        if self.native_vlan is not None:
            _validate_vlan(self.native_vlan)
        # native VLAN должен присутствовать в списке разрешённых
        if self.native_vlan is not None and self.native_vlan not in self.vlan_ids:
            raise ValidationError(
                f"native_vlan {self.native_vlan} отсутствует в vlan_ids"
            )
        # Дедупликация и сортировка
        self.vlan_ids = tuple(sorted(set(self.vlan_ids)))

    def update(
        self,
        *,
        name: str | None = None,
        vlan_ids: tuple[int, ...] | None = None,
        native_vlan: Any = _UNSET,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        """Обновляет параметры транкового порта."""
        if name is not None:
            self.name = name
        effective_vlans = self.vlan_ids
        if vlan_ids is not None:
            for v in vlan_ids:
                _validate_vlan(v)
            effective_vlans = tuple(sorted(set(vlan_ids)))
            self.vlan_ids = effective_vlans
        if native_vlan is not _UNSET:
            if native_vlan is not None:
                _validate_vlan(native_vlan)
                if native_vlan not in effective_vlans:
                    raise ValidationError(
                        f"native_vlan {native_vlan} отсутствует в vlan_ids"
                    )
            self.native_vlan = native_vlan
        if labels is not None:
            self.labels = labels
        self.updated_at = now
