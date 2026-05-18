"""Граф топологии: узлы + сети + наблюдаемые мосты + связи.

Это снимок состояния системы, собираемый из ``NodeRepository``,
``NetworkRepository`` и ``ObservedStateRepository``. Используется для
``GET /topology`` (SDN-026) и как вход для drift-detection (SDN-027).

Граф плоский: списки узлов/сетей/мостов + список рёбер. Это удобно для
сериализации в JSON и для рендера в UI/CLI: клиент сам соединяет
``edge.source`` и ``edge.target`` по их id, мы не строим вложенную
иерархию.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sdn_controller.core.entities.network import Network, Subnet
from sdn_controller.core.entities.node import Node
from sdn_controller.core.entities.observed_state import ObservedBridge
from sdn_controller.core.value_objects.enums import NetworkType, NodeStatus
from sdn_controller.core.value_objects.ids import NetworkId, NodeId

EdgeKind = Literal["node_network", "vxlan_tunnel"]


@dataclass(frozen=True, slots=True)
class TopologyNode:
    """Узел как его видит топология: всё то же, что в ``Node``, плюс
    последняя наблюдённая `state_hash` (или ``None``, если узел ни разу
    не отчитывался)."""

    id: NodeId
    name: str
    mgmt_ip: str
    status: NodeStatus
    roles: tuple[str, ...] = ()
    labels: dict[str, str] = field(default_factory=dict)
    last_seen_at: datetime | None = None
    observed_state_hash: str | None = None
    observed_at: datetime | None = None

    @classmethod
    def from_domain(
        cls,
        node: Node,
        *,
        observed_hash: str | None,
        observed_at: datetime | None,
    ) -> TopologyNode:
        return cls(
            id=node.id,
            name=node.name,
            mgmt_ip=node.mgmt_ip,
            status=node.status,
            roles=tuple(node.roles),
            labels=dict(node.labels),
            last_seen_at=node.last_seen_at,
            observed_state_hash=observed_hash,
            observed_at=observed_at,
        )


@dataclass(frozen=True, slots=True)
class TopologyNetwork:
    """Сеть как описание интента (что мы хотим), не как реальность."""

    id: NetworkId
    name: str
    type: NetworkType
    mtu: int
    vlan_id: int | None
    vni: int | None
    subnet: Subnet | None
    node_ids: tuple[NodeId, ...]
    intent_version: int
    spec_hash: str

    @classmethod
    def from_domain(cls, network: Network) -> TopologyNetwork:
        return cls(
            id=network.id,
            name=network.name,
            type=network.type,
            mtu=network.mtu,
            vlan_id=network.vlan_id,
            vni=network.vni,
            subnet=network.subnet,
            node_ids=tuple(network.node_ids),
            intent_version=network.intent_version,
            spec_hash=network.spec_hash,
        )


@dataclass(frozen=True, slots=True)
class TopologyBridge:
    """Мост, привязанный к конкретному узлу.

    ``network_id`` восстанавливается из ``external_ids[network_id]`` —
    если мост наш (``owner=sdn-controller``), мы знаем, к какой сети он
    относится. Если ключа нет (мост чужой), ``network_id`` остаётся ``None``.
    """

    node_id: NodeId
    name: str
    datapath_type: str
    external_ids: dict[str, str]
    network_id: NetworkId | None = None

    @classmethod
    def from_observed(cls, *, node_id: NodeId, bridge: ObservedBridge) -> TopologyBridge:
        nid_raw = bridge.external_ids.get("network_id")
        return cls(
            node_id=node_id,
            name=bridge.name,
            datapath_type=bridge.datapath_type,
            external_ids=dict(bridge.external_ids),
            network_id=NetworkId(nid_raw) if nid_raw else None,
        )


@dataclass(frozen=True, slots=True)
class TopologyEdge:
    """Связь между двумя сущностями графа.

    ``kind`` различает смысл рёбер:

    * ``node_network`` — узел является членом сети (по ``network.node_ids``);
    * ``vxlan_tunnel`` — между двумя узлами VXLAN-сети существует
      туннель (диф-инжину этого достаточно, чтобы рисовать в UI).
    """

    kind: EdgeKind
    source: str  # NodeId
    target: str  # NodeId или NetworkId — зависит от ``kind``
    network_id: NetworkId | None = None


@dataclass(frozen=True, slots=True)
class Topology:
    """Полный снимок графа на момент ``observed_at``."""

    observed_at: datetime
    nodes: tuple[TopologyNode, ...]
    networks: tuple[TopologyNetwork, ...]
    bridges: tuple[TopologyBridge, ...]
    edges: tuple[TopologyEdge, ...]


__all__ = [
    "EdgeKind",
    "Topology",
    "TopologyBridge",
    "TopologyEdge",
    "TopologyNetwork",
    "TopologyNode",
]
