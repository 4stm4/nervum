"""Planner fan-out for a single network across multiple nodes."""

from __future__ import annotations

from datetime import UTC, datetime

from sdn_controller.core.entities import (
    Network,
    Node,
    ObservedBridge,
    ObservedInterface,
    ObservedPort,
    ObservedState,
)
from sdn_controller.core.services.diff_engine import (
    NETWORK_KEY,
    OWNER_KEY,
    OWNER_LABEL,
    NodeAddress,
    bridge_name,
    vxlan_port_name,
)
from sdn_controller.core.services.planner import Planner
from sdn_controller.core.value_objects.enums import NetworkType
from sdn_controller.core.value_objects.ids import NetworkId, NodeId
from sdn_controller.ports.agent import EnsureBridgeStep, EnsureVxlanPortStep
from tests.conftest import CountingIdFactory

_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def _node(node_id: str, ip: str) -> Node:
    return Node(
        id=NodeId(node_id),
        name=node_id,
        mgmt_ip=ip,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _vxlan_network(node_ids: tuple[NodeId, ...]) -> Network:
    return Network(
        id=NetworkId("net_1"),
        name="prod",
        type=NetworkType.VXLAN,
        vni=10100,
        created_at=_NOW,
        updated_at=_NOW,
        node_ids=node_ids,
    )


def test_three_node_vxlan_planner_emits_three_plans_each_with_two_peers(
    ids: CountingIdFactory,
) -> None:
    planner = Planner(ids=ids)
    nodes = {
        NodeId("node_a"): _node("node_a", "10.0.0.1"),
        NodeId("node_b"): _node("node_b", "10.0.0.2"),
        NodeId("node_c"): _node("node_c", "10.0.0.3"),
    }
    network = _vxlan_network(tuple(nodes.keys()))

    plans = planner.plan_for_network(
        network=network,
        nodes=nodes,
        observed_by_node={},
    )

    assert {p.node_id for p in plans} == set(nodes)
    for per_node in plans:
        bridges = [s for s in per_node.plan.steps if isinstance(s, EnsureBridgeStep)]
        vxlans = [s for s in per_node.plan.steps if isinstance(s, EnsureVxlanPortStep)]
        assert len(bridges) == 1
        # Each node has tunnels to the two *other* nodes.
        assert len(vxlans) == 2
        remote_ips = {s.remote_ip for s in vxlans}
        assert remote_ips == {n.mgmt_ip for nid, n in nodes.items() if nid != per_node.node_id}


def test_planner_skips_nodes_with_matching_state(ids: CountingIdFactory) -> None:
    """A converged node yields an empty diff, so the planner omits its plan."""
    planner = Planner(ids=ids)
    nodes = {
        NodeId("node_a"): _node("node_a", "10.0.0.1"),
        NodeId("node_b"): _node("node_b", "10.0.0.2"),
    }
    network = _vxlan_network(tuple(nodes.keys()))
    converged_a = ObservedState(
        node_id=NodeId("node_a"),
        observed_at=_NOW,
        state_hash="abc",
        bridges=(
            ObservedBridge(
                name=bridge_name(network),
                external_ids={OWNER_KEY: OWNER_LABEL, NETWORK_KEY: network.id},
                ports=(
                    ObservedPort(
                        name=vxlan_port_name(
                            vni=network.vni or 0,
                            remote=NodeAddress(NodeId("node_b"), "10.0.0.2"),
                        ),
                        external_ids={OWNER_KEY: OWNER_LABEL, NETWORK_KEY: network.id},
                        interfaces=(
                            ObservedInterface(
                                name="vxi",
                                type="vxlan",
                                options={
                                    "key": str(network.vni),
                                    "remote_ip": "10.0.0.2",
                                    "dst_port": "4789",
                                    "mtu_request": str(network.mtu),
                                },
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    plans = planner.plan_for_network(
        network=network,
        nodes=nodes,
        observed_by_node={NodeId("node_a"): converged_a},
    )

    # Only node_b (empty observed) needs a plan; node_a is already compliant.
    assert [p.node_id for p in plans] == [NodeId("node_b")]
