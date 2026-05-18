"""Сборка графа топологии (SDN-026) и поиск дрейфа (SDN-027).

``GetTopology`` собирает плоский граф из репозиториев без обращения к
агенту: всё, что нам нужно, уже лежит в БД (intent в
``NetworkRepository``, последний observed state в
``ObservedStateRepository``). Это делает endpoint безопасным к спаму со
стороны UI: запросы не дёргают агенты.

``ScanDrift`` пере-использует ``diff_for_node`` из M5 — она уже умеет
сравнивать desired с observed и возвращать список шагов. Мы прогоняем
её на каждую пару (network, member node), отфильтровываем edge-service
шаги (см. комментарий в drift.py) и упаковываем результат.
"""

from __future__ import annotations

from sdn_controller.core.entities import (
    DriftItem,
    DriftReport,
    Network,
    Node,
    ObservedState,
    Topology,
    TopologyBridge,
    TopologyEdge,
    TopologyNetwork,
    TopologyNode,
)
from sdn_controller.core.entities.drift import DriftKind
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.services.diff_engine import (
    NodeAddress,
    diff_for_node,
)
from sdn_controller.core.value_objects.enums import NetworkType
from sdn_controller.core.value_objects.ids import NetworkId, NodeId
from sdn_controller.ports.agent import (
    DeleteBridgeStep,
    DeletePortStep,
    EnsureBridgeStep,
    EnsurePortStep,
    EnsureVxlanPortStep,
    PlanStep,
)
from sdn_controller.ports.persistence import (
    NetworkRepository,
    NodeRepository,
    ObservedStateRepository,
)

# Минимум узлов, между которыми появляется хотя бы один VXLAN-туннель.
_MIN_VXLAN_MESH_NODES = 2


class GetTopology:
    """Снимок топологии: узлы, сети, наблюдаемые мосты, рёбра."""

    def __init__(
        self,
        *,
        nodes: NodeRepository,
        networks: NetworkRepository,
        observed_states: ObservedStateRepository,
        clock: Clock,
    ) -> None:
        self._nodes = nodes
        self._networks = networks
        self._observed = observed_states
        self._clock = clock

    async def execute(self) -> Topology:
        nodes = await self._nodes.list()
        networks = await self._networks.list()

        # observed state — по одному запросу на узел; репозиторий уже
        # eager-loads payload, так что N+1 нам не грозит.
        observed_by_node: dict[NodeId, ObservedState] = {}
        for node in nodes:
            obs = await self._observed.get(node.id)
            if obs is not None:
                observed_by_node[node.id] = obs

        topo_nodes: list[TopologyNode] = []
        for node in nodes:
            obs = observed_by_node.get(node.id)
            topo_nodes.append(
                TopologyNode.from_domain(
                    node,
                    observed_hash=obs.state_hash if obs is not None else None,
                    observed_at=obs.observed_at if obs is not None else None,
                )
            )

        topo_networks = tuple(TopologyNetwork.from_domain(n) for n in networks)

        bridges: list[TopologyBridge] = []
        for node_id, observed in observed_by_node.items():
            for b in observed.bridges:
                bridges.append(TopologyBridge.from_observed(node_id=node_id, bridge=b))

        edges = _build_edges(networks)

        return Topology(
            observed_at=self._clock.now(),
            nodes=tuple(topo_nodes),
            networks=topo_networks,
            bridges=tuple(bridges),
            edges=tuple(edges),
        )


def _build_edges(networks: list[Network]) -> list[TopologyEdge]:
    """Сложить рёбра node↔network и VXLAN-туннели между узлами."""
    edges: list[TopologyEdge] = []
    for network in networks:
        for node_id in network.node_ids:
            edges.append(
                TopologyEdge(
                    kind="node_network",
                    source=node_id,
                    target=network.id,
                    network_id=NetworkId(network.id),
                )
            )
        # VXLAN-туннели только для VXLAN-сетей: один шаг — одна пара узлов.
        # Симметричное ребро (a-b и b-a) не интересно для UI, поэтому
        # эмитим один раз для каждой неупорядоченной пары.
        if network.type is NetworkType.VXLAN and len(network.node_ids) >= _MIN_VXLAN_MESH_NODES:
            members = list(network.node_ids)
            for i, a in enumerate(members):
                for b in members[i + 1 :]:
                    edges.append(
                        TopologyEdge(
                            kind="vxlan_tunnel",
                            source=a,
                            target=b,
                            network_id=NetworkId(network.id),
                        )
                    )
    return edges


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------


class ScanDrift:
    """Прогоняет ``diff_for_node`` по каждой паре (сеть, член-узел) и
    пакует расхождения в ``DriftReport``.

    Использует **закэшированный** observed state — мы не дёргаем агента
    из этого use case, чтобы оператор мог запускать ``/drift`` сколь
    угодно часто. Если для какого-то узла observed state отсутствует,
    узел попадает в ``stale_nodes`` (а не в `items` с пустыми
    подробностями) — лучше явная неопределённость, чем тихий ноль.
    """

    def __init__(
        self,
        *,
        nodes: NodeRepository,
        networks: NetworkRepository,
        observed_states: ObservedStateRepository,
        clock: Clock,
    ) -> None:
        self._nodes = nodes
        self._networks = networks
        self._observed = observed_states
        self._clock = clock

    async def execute(self) -> DriftReport:
        networks = await self._networks.list()
        items: list[DriftItem] = []
        stale: set[NodeId] = set()

        for network in networks:
            if not network.node_ids:
                continue
            # peers — фиксированный список адресов всех узлов сети, его
            # используют VXLAN-сравнения внутри diff_for_node.
            peers = await self._peers_for_network(network)
            for node_id in network.node_ids:
                observed = await self._observed.get(node_id)
                if observed is None:
                    stale.add(node_id)
                    continue
                steps = diff_for_node(
                    network=network,
                    local_node_id=node_id,
                    peers=peers,
                    observed=observed,
                )
                for step in steps:
                    drift = _step_to_drift_item(
                        network_id=NetworkId(network.id),
                        node_id=node_id,
                        step=step,
                    )
                    if drift is not None:
                        items.append(drift)

        return DriftReport(
            scanned_at=self._clock.now(),
            items=tuple(items),
            stale_nodes=tuple(sorted(stale)),
        )

    async def _peers_for_network(self, network: Network) -> list[NodeAddress]:
        peers: list[NodeAddress] = []
        for node_id in network.node_ids:
            node: Node | None = await self._nodes.get(node_id)
            if node is None:
                # Узел был удалён уже после сохранения сети — для drift
                # это просто пропуск (адрес для VXLAN-туннеля неизвестен).
                continue
            peers.append(NodeAddress(node_id=node.id, mgmt_ip=node.mgmt_ip))
        return peers


def _step_to_drift_item(
    *,
    network_id: NetworkId,
    node_id: NodeId,
    step: PlanStep,
) -> DriftItem | None:
    """Преобразовать структурный шаг плана в ``DriftItem``.

    Edge-service шаги (DHCP/DNS/NAT/FW) сюда не попадают: они
    эмитятся всегда (агент сам отвечает за идемпотентность), поэтому
    их интерпретация как «дрейф» нечестная. Это согласуется с
    ``is_in_compliance``.
    """
    kind: DriftKind
    description: str
    payload: dict[str, object]
    match step:
        case EnsureBridgeStep():
            kind = "bridge_missing_or_changed"
            description = f"bridge {step.name!r} отсутствует или его external_ids разошлись"
            payload = {"bridge": step.name, "external_ids": dict(step.external_ids)}
        case DeleteBridgeStep():
            kind = "bridge_orphan"
            description = f"наш bridge {step.name!r} не должен здесь существовать"
            payload = {"bridge": step.name}
        case EnsureVxlanPortStep():
            kind = "vxlan_port_missing_or_changed"
            description = (
                f"VXLAN-порт {step.name!r} на bridge {step.bridge!r} отсутствует или поменялся"
            )
            payload = {
                "bridge": step.bridge,
                "port": step.name,
                "vni": step.vni,
                "remote_ip": step.remote_ip,
            }
        case EnsurePortStep():
            kind = "port_missing_or_changed"
            description = f"порт {step.name!r} на bridge {step.bridge!r} отсутствует или поменялся"
            payload = {"bridge": step.bridge, "port": step.name, "type": step.type}
        case DeletePortStep():
            kind = "port_orphan"
            description = f"наш порт {step.name!r} на bridge {step.bridge!r} лишний"
            payload = {"bridge": step.bridge, "port": step.name}
        case _:
            # Edge-service и любые ensure_*-варианты, которые не считаем
            # дрейфом, просто игнорируем.
            return None
    return DriftItem(
        network_id=network_id,
        node_id=node_id,
        kind=kind,
        description=description,
        payload=payload,
    )


__all__ = ["GetTopology", "ScanDrift"]
