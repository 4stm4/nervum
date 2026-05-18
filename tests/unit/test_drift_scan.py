"""Unit-тесты ``ScanDrift``.

Используем тот же diff-инжин, что и reconciler, но кормим его
закэшированным observed state. Покрываем три сценария:
* всё сошлось → пустой отчёт;
* мост отсутствует → ``bridge_missing_or_changed``;
* observed state ни разу не сохранялся → узел уходит в ``stale_nodes``.
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
    ObservedInterface,
    ObservedPort,
    ObservedState,
)
from sdn_controller.core.use_cases.topology import ScanDrift
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


def _converged_observed(*, network_id: str, br_name: str, vni: int, peer_ip: str) -> ObservedState:
    """Состояние, которое diff_for_node должен считать соответствующим."""
    return ObservedState(
        node_id=NodeId("node_a"),
        observed_at=_NOW,
        state_hash="state",
        bridges=(
            ObservedBridge(
                name=br_name,
                external_ids={"owner": "sdn-controller", "network_id": network_id},
                ports=(
                    ObservedPort(
                        name=f"vx-{vni}-ode_b_",  # формирование имени стабильно, см. diff_engine
                        external_ids={"owner": "sdn-controller", "network_id": network_id},
                        interfaces=(
                            ObservedInterface(
                                name="ignored",
                                type="vxlan",
                                options={
                                    "key": str(vni),
                                    "remote_ip": peer_ip,
                                    "dst_port": "4789",
                                    "mtu_request": "1500",
                                },
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


@pytest.fixture
def use_case(
    clock: FrozenClock,
) -> tuple[
    ScanDrift,
    InMemoryNodeRepository,
    InMemoryNetworkRepository,
    InMemoryObservedStateRepository,
]:
    nodes = InMemoryNodeRepository()
    networks = InMemoryNetworkRepository()
    observed = InMemoryObservedStateRepository()
    return (
        ScanDrift(nodes=nodes, networks=networks, observed_states=observed, clock=clock),
        nodes,
        networks,
        observed,
    )


async def test_empty_state_is_no_drift(
    use_case: tuple[
        ScanDrift,
        InMemoryNodeRepository,
        InMemoryNetworkRepository,
        InMemoryObservedStateRepository,
    ],
) -> None:
    uc, _, _, _ = use_case
    report = await uc.execute()
    assert report.items == ()
    assert report.stale_nodes == ()


async def test_missing_bridge_surfaces_drift_item(
    use_case: tuple[
        ScanDrift,
        InMemoryNodeRepository,
        InMemoryNetworkRepository,
        InMemoryObservedStateRepository,
    ],
) -> None:
    uc, nodes, networks, observed = use_case
    a = _node("node_a", "10.0.0.1")
    b = _node("node_b", "10.0.0.2")
    await nodes.save(a)
    await nodes.save(b)
    await networks.save(_vxlan("prod", 10100, (a.id, b.id)))

    # Узлы записали observed, но ни моста, ни VXLAN-портов там нет.
    for node_id in (a.id, b.id):
        await observed.save(ObservedState(node_id=node_id, observed_at=_NOW, state_hash="0"))

    report = await uc.execute()

    kinds = {(it.node_id, it.kind) for it in report.items}
    assert ("node_a", "bridge_missing_or_changed") in kinds
    assert ("node_b", "bridge_missing_or_changed") in kinds
    # И сами VXLAN-туннели тоже считаются дрейфом — по одному на пару.
    vxlan_drift = [it for it in report.items if it.kind == "vxlan_port_missing_or_changed"]
    assert len(vxlan_drift) == 2  # один с node_a-на-node_b, один с node_b-на-node_a
    assert all(it.network_id == "net_prod" for it in report.items)


async def test_stale_node_is_reported_separately(
    use_case: tuple[
        ScanDrift,
        InMemoryNodeRepository,
        InMemoryNetworkRepository,
        InMemoryObservedStateRepository,
    ],
) -> None:
    uc, nodes, networks, observed = use_case
    a = _node("node_a", "10.0.0.1")
    b = _node("node_b", "10.0.0.2")
    await nodes.save(a)
    await nodes.save(b)
    await networks.save(_vxlan("prod", 10100, (a.id, b.id)))
    # Только node_a отчитывался observed state.
    await observed.save(ObservedState(node_id=a.id, observed_at=_NOW, state_hash="hash"))

    report = await uc.execute()

    assert "node_b" in report.stale_nodes
    # И при этом дрейф для node_b не эмитим, чтобы не врать.
    assert not any(it.node_id == "node_b" for it in report.items)


async def test_empty_network_membership_is_skipped(
    use_case: tuple[
        ScanDrift,
        InMemoryNodeRepository,
        InMemoryNetworkRepository,
        InMemoryObservedStateRepository,
    ],
) -> None:
    uc, _, networks, _ = use_case
    # сеть без node_ids — нечего проверять, дрейфа быть не может.
    await networks.save(_vxlan("orphan", 10200, ()))

    report = await uc.execute()
    assert report.items == ()
    assert report.stale_nodes == ()
