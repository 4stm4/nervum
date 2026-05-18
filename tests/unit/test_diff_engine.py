"""Diff engine: VXLAN mesh creation, idempotency, cleanup of orphans."""

from __future__ import annotations

from datetime import UTC, datetime

from sdn_controller.core.entities import (
    Network,
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
    diff_for_node,
    is_in_compliance,
    vxlan_port_name,
)
from sdn_controller.core.value_objects.enums import NetworkType
from sdn_controller.core.value_objects.ids import NetworkId, NodeId
from sdn_controller.ports.agent import (
    DeleteBridgeStep,
    EnsureBridgeStep,
    EnsureVxlanPortStep,
)

_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def _vxlan_network(node_ids: tuple[NodeId, ...]) -> Network:
    return Network(
        id=NetworkId("net_1"),
        name="prod",
        type=NetworkType.VXLAN,
        vni=10100,
        mtu=1450,
        created_at=_NOW,
        updated_at=_NOW,
        node_ids=node_ids,
    )


def _empty_observed(node_id: NodeId) -> ObservedState:
    return ObservedState(node_id=node_id, observed_at=_NOW, state_hash="0" * 64)


def _peers(*addrs: tuple[NodeId, str]) -> list[NodeAddress]:
    return [NodeAddress(node_id=n, mgmt_ip=ip) for n, ip in addrs]


# ---------------------------------------------------------------------------
# Empty observed → full creation
# ---------------------------------------------------------------------------


def test_empty_node_gets_bridge_and_full_vxlan_mesh() -> None:
    nodes = (NodeId("node_a"), NodeId("node_b"), NodeId("node_c"))
    network = _vxlan_network(nodes)
    observed = _empty_observed(NodeId("node_a"))
    peers = _peers(
        (NodeId("node_a"), "10.0.0.1"),
        (NodeId("node_b"), "10.0.0.2"),
        (NodeId("node_c"), "10.0.0.3"),
    )

    steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=peers,
        observed=observed,
    )

    # One bridge + two vxlan ports (peers excluding self).
    bridge_steps = [s for s in steps if isinstance(s, EnsureBridgeStep)]
    vxlan_steps = [s for s in steps if isinstance(s, EnsureVxlanPortStep)]
    assert len(bridge_steps) == 1
    assert bridge_steps[0].name == bridge_name(network)
    assert bridge_steps[0].external_ids == {OWNER_KEY: OWNER_LABEL, NETWORK_KEY: network.id}
    assert len(vxlan_steps) == 2
    remote_targets = {(s.remote_ip, s.vni) for s in vxlan_steps}
    assert remote_targets == {("10.0.0.2", 10100), ("10.0.0.3", 10100)}
    # MTU and external_ids propagate.
    for s in vxlan_steps:
        assert s.mtu == network.mtu
        assert s.external_ids == {OWNER_KEY: OWNER_LABEL, NETWORK_KEY: network.id}


def test_vxlan_port_names_are_stable_and_short() -> None:
    network = _vxlan_network((NodeId("node_long_name_a"), NodeId("node_long_name_b")))
    name = vxlan_port_name(
        vni=network.vni or 0,
        remote=NodeAddress(node_id=NodeId("node_long_name_b"), mgmt_ip="x"),
    )

    assert name.startswith("vx-10100-")
    assert len(name) <= 15  # safe for kernel iface limits


# ---------------------------------------------------------------------------
# No-op when state already matches
# ---------------------------------------------------------------------------


def test_diff_is_noop_when_state_matches() -> None:
    nodes = (NodeId("node_a"), NodeId("node_b"))
    network = _vxlan_network(nodes)
    peers = _peers((NodeId("node_a"), "10.0.0.1"), (NodeId("node_b"), "10.0.0.2"))

    observed = ObservedState(
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
                                name="ignored",
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

    assert is_in_compliance(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=peers,
        observed=observed,
    )
    # diff_for_node still emits ``ensure_bridge`` + ``ensure_vxlan_port`` —
    # they're idempotent at the agent layer; only deletes are conditional.
    steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=peers,
        observed=observed,
    )
    assert not any(isinstance(s, DeleteBridgeStep) for s in steps)


# ---------------------------------------------------------------------------
# Cleanup of orphaned owned bridges
# ---------------------------------------------------------------------------


def test_orphan_owned_bridge_is_deleted() -> None:
    network = _vxlan_network((NodeId("node_a"),))
    observed = ObservedState(
        node_id=NodeId("node_a"),
        observed_at=_NOW,
        state_hash="abc",
        bridges=(
            ObservedBridge(
                name="br-prod-old",  # used to be ours, name changed
                external_ids={OWNER_KEY: OWNER_LABEL, NETWORK_KEY: network.id},
            ),
            ObservedBridge(
                name=bridge_name(network),
                external_ids={OWNER_KEY: OWNER_LABEL, NETWORK_KEY: network.id},
            ),
            ObservedBridge(
                name="br-someone-else",  # not ours — leave alone
                external_ids={"owner": "other"},
            ),
        ),
    )

    steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=_peers((NodeId("node_a"), "10.0.0.1")),
        observed=observed,
    )

    deletes = [s for s in steps if isinstance(s, DeleteBridgeStep)]
    assert [d.name for d in deletes] == ["br-prod-old"]


def test_node_no_longer_in_network_triggers_full_cleanup() -> None:
    network = _vxlan_network((NodeId("node_b"),))  # node_a no longer included
    observed = ObservedState(
        node_id=NodeId("node_a"),
        observed_at=_NOW,
        state_hash="abc",
        bridges=(
            ObservedBridge(
                name=bridge_name(network),
                external_ids={OWNER_KEY: OWNER_LABEL, NETWORK_KEY: network.id},
            ),
            ObservedBridge(name="br-other", external_ids={"owner": "other"}),
        ),
    )

    steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=_peers((NodeId("node_b"), "10.0.0.2")),
        observed=observed,
    )

    deletes = [s for s in steps if isinstance(s, DeleteBridgeStep)]
    assert [d.name for d in deletes] == [bridge_name(network)]
