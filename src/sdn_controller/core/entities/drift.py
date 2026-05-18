"""Drift-снимок: чем реальность отличается от интента.

Каждый ``DriftItem`` — это конкретный шаг, который привёл бы узел в
соответствие с интентом сети, если бы мы прямо сейчас сделали
``apply``. Это та же информация, что выдаёт ``diff_for_node``, но без
edge-service шагов: на агенте мы не наблюдаем DHCP/DNS/NAT/FW
state-by-state, поэтому называть их «дрейфом» нечестно — за их
идемпотентность отвечает агент.

Назначение M8: оператор видит "вот что разошлось", не выполняя сам
``apply``. Reconciler не зовётся, агент не дёргается лишний раз.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sdn_controller.core.value_objects.ids import NetworkId, NodeId

DriftKind = Literal[
    "bridge_missing_or_changed",
    "bridge_orphan",
    "vxlan_port_missing_or_changed",
    "port_missing_or_changed",
    "port_orphan",
]


@dataclass(frozen=True, slots=True)
class DriftItem:
    """Один пункт дрейфа.

    ``description`` — человеко-читаемая строка для UI; ``payload`` несёт
    структурированные детали (например, имя моста или порта), чтобы CLI
    мог фильтровать без regexp по тексту.
    """

    network_id: NetworkId
    node_id: NodeId
    kind: DriftKind
    description: str
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DriftReport:
    """Снимок дрейфа на момент ``scanned_at``.

    Пустой ``items`` означает «всё сошлось»; ``stale_nodes`` —
    узлы-члены сетей, для которых ещё не был сохранён observed state
    (значит, мы не знаем, есть дрейф или нет — оператору это знать
    полезнее, чем замалчивать).
    """

    scanned_at: datetime
    items: tuple[DriftItem, ...] = ()
    stale_nodes: tuple[NodeId, ...] = ()


__all__ = ["DriftItem", "DriftKind", "DriftReport"]
