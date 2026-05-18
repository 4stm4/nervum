"""Unit-тесты ``GetTopology``.

Граф собирается из in-memory репозиториев без обращения к агенту. Мы
проверяем структуру (узлы/сети/мосты/рёбра) и привязку моста к сети
через ``external_ids[network_id]``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdn_controller.adapters.memory import (
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryObservedStateRepository,
)
from sdn_controller.core.entities import (
    Network,
    Node,
    ObservedBridge,
    ObservedState,
)
from sdn_controller.core.use_cases.topology import GetTopology
from sdn_controller.core.value_objects.enums import NetworkType, NodeStatus
from sdn_controller.core.value_objects.ids import NetworkId, NodeId
from tests.conftest import FrozenClock

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)


def _node(node_id: str, mgmt_ip: str) -> Node:
    return Node(
        id=NodeId(node_id),
        name=node_id,
        mgmt_ip=mgmt_ip,
        status=NodeStatus.ONLINE,
        created_at=_NOW,
        updated_at=_NOW,
        last_seen_at=_NOW,
    )


def _vxlan(name: str, vni: int, members: tuple[NodeId, ...]) -> Network:
    return Network(
        id=NetworkId(f"net_{name}"),
        name=name,
        type=NetworkType.VXLAN,
        vni=vni,
        created_at=_NOW,
        updated_at=_NOW,
        node_ids=members,
    )


@pytest.fixture
def use_case(
    clock: FrozenClock,
) -> tuple[
    GetTopology,
    InMemoryNodeRepository,
    InMemoryNetworkRepository,
    InMemoryObservedStateRepository,
]:
    nodes = InMemoryNodeRepository()
    networks = InMemoryNetworkRepository()
    observed = InMemoryObservedStateRepository()
    uc = GetTopology(nodes=nodes, networks=networks, observed_states=observed, clock=clock)
    return uc, nodes, networks, observed


async def test_empty_repos_return_empty_topology(
    use_case: tuple[
        GetTopology,
        InMemoryNodeRepository,
        InMemoryNetworkRepository,
        InMemoryObservedStateRepository,
    ],
    clock: FrozenClock,
) -> None:
    uc, _, _, _ = use_case
    topo = await uc.execute()

    assert topo.observed_at == clock.current
    assert topo.nodes == ()
    assert topo.networks == ()
    assert topo.bridges == ()
    assert topo.edges == ()


async def test_topology_includes_nodes_networks_and_edges(
    use_case: tuple[
        GetTopology,
        InMemoryNodeRepository,
        InMemoryNetworkRepository,
        InMemoryObservedStateRepository,
    ],
) -> None:
    uc, nodes, networks, _ = use_case
    a = _node("node_a", "10.0.0.1")
    b = _node("node_b", "10.0.0.2")
    c = _node("node_c", "10.0.0.3")
    await nodes.save(a)
    await nodes.save(b)
    await nodes.save(c)
    network = _vxlan("prod", 10100, (a.id, b.id, c.id))
    await networks.save(network)

    topo = await uc.execute()

    assert {n.id for n in topo.nodes} == {"node_a", "node_b", "node_c"}
    assert {n.id for n in topo.networks} == {"net_prod"}
    # node_network: по одному ребру на каждого члена.
    membership = [e for e in topo.edges if e.kind == "node_network"]
    assert sorted((e.source, e.target) for e in membership) == [
        ("node_a", "net_prod"),
        ("node_b", "net_prod"),
        ("node_c", "net_prod"),
    ]
    # vxlan_tunnel: неупорядоченных пар три (a-b, a-c, b-c).
    tunnels = [e for e in topo.edges if e.kind == "vxlan_tunnel"]
    assert len(tunnels) == 3
    pairs = {tuple(sorted((e.source, e.target))) for e in tunnels}
    assert pairs == {("node_a", "node_b"), ("node_a", "node_c"), ("node_b", "node_c")}


async def test_topology_associates_bridges_with_networks_via_external_ids(
    use_case: tuple[
        GetTopology,
        InMemoryNodeRepository,
        InMemoryNetworkRepository,
        InMemoryObservedStateRepository,
    ],
) -> None:
    uc, nodes, networks, observed_states = use_case
    a = _node("node_a", "10.0.0.1")
    await nodes.save(a)
    network = _vxlan("prod", 10100, (a.id,))
    await networks.save(network)

    # Наш мост — со ссылкой на сеть. Чужой мост — без ссылки.
    state = ObservedState(
        node_id=a.id,
        observed_at=_NOW,
        state_hash="hash",
        bridges=(
            ObservedBridge(
                name="br-prod",
                external_ids={"owner": "sdn-controller", "network_id": "net_prod"},
            ),
            ObservedBridge(name="br-not-ours", external_ids={"owner": "other"}),
        ),
    )
    await observed_states.save(state)

    topo = await uc.execute()

    assert len(topo.bridges) == 2
    ours = next(b for b in topo.bridges if b.name == "br-prod")
    assert ours.network_id == "net_prod"
    assert ours.node_id == "node_a"
    other = next(b for b in topo.bridges if b.name == "br-not-ours")
    assert other.network_id is None


async def test_topology_reports_observed_hash_per_node(
    use_case: tuple[
        GetTopology,
        InMemoryNodeRepository,
        InMemoryNetworkRepository,
        InMemoryObservedStateRepository,
    ],
) -> None:
    uc, nodes, _, observed_states = use_case
    a = _node("node_a", "10.0.0.1")
    b = _node("node_b", "10.0.0.2")
    await nodes.save(a)
    await nodes.save(b)
    await observed_states.save(
        ObservedState(node_id=a.id, observed_at=_NOW, state_hash="hash_a"),
    )
    # node_b — без observed state.

    topo = await uc.execute()

    by_id = {str(n.id): n for n in topo.nodes}
    assert by_id["node_a"].observed_state_hash == "hash_a"
    assert by_id["node_a"].observed_at == _NOW
    assert by_id["node_b"].observed_state_hash is None
    assert by_id["node_b"].observed_at is None
