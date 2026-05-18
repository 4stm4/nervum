"""Diff engine — M7 edge service emission.

The diff engine designates the first node in ``network.node_ids`` as the
edge node and emits DHCP/DNS/NAT/firewall steps there (only). The other
nodes get the OVS bridge but never an edge-service step.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sdn_controller.core.entities import Network, ObservedState, Subnet
from sdn_controller.core.services.diff_engine import (
    NodeAddress,
    diff_for_node,
    is_edge_node,
)
from sdn_controller.core.value_objects.edge_services import (
    DhcpSpec,
    FirewallAction,
    FirewallPolicy,
    FirewallProto,
    FirewallRule,
    NatSpec,
)
from sdn_controller.core.value_objects.enums import NetworkType
from sdn_controller.core.value_objects.ids import NetworkId, NodeId, SubnetId
from sdn_controller.ports.agent import (
    EnsureDhcpScopeStep,
    EnsureDnsZoneStep,
    EnsureFirewallPolicyStep,
    EnsureNatRuleStep,
)

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)


def _network(
    *,
    nodes: tuple[NodeId, ...],
    subnet: Subnet | None = None,
    nat: NatSpec | None = None,
    firewall: FirewallPolicy | None = None,
) -> Network:
    return Network(
        id=NetworkId("net_1"),
        name="prod",
        type=NetworkType.VXLAN,
        vni=10100,
        created_at=_NOW,
        updated_at=_NOW,
        node_ids=nodes,
        subnet=subnet,
        nat=nat,
        firewall_policy=firewall,
    )


def _observed(node_id: NodeId) -> ObservedState:
    return ObservedState(node_id=node_id, observed_at=_NOW, state_hash="0" * 64)


def test_edge_node_is_first_in_node_ids() -> None:
    network = _network(nodes=(NodeId("node_a"), NodeId("node_b")))
    assert is_edge_node(network=network, local_node_id=NodeId("node_a"))
    assert not is_edge_node(network=network, local_node_id=NodeId("node_b"))


def test_dhcp_step_emitted_only_on_edge_node() -> None:
    subnet = Subnet(
        id=SubnetId("sn_1"),
        cidr="10.0.0.0/24",
        gateway="10.0.0.1",
        dns_servers=("10.0.0.2",),
        dhcp=DhcpSpec(range_start="10.0.0.50", range_end="10.0.0.100"),
    )
    network = _network(
        nodes=(NodeId("node_a"), NodeId("node_b")),
        subnet=subnet,
    )

    edge_steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=[NodeAddress(NodeId("node_b"), "10.0.0.2")],
        observed=_observed(NodeId("node_a")),
    )
    non_edge_steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_b"),
        peers=[NodeAddress(NodeId("node_a"), "10.0.0.1")],
        observed=_observed(NodeId("node_b")),
    )

    dhcp_steps = [s for s in edge_steps if isinstance(s, EnsureDhcpScopeStep)]
    assert len(dhcp_steps) == 1
    spec = dhcp_steps[0].spec
    assert spec.cidr == "10.0.0.0/24"
    assert spec.range_start == "10.0.0.50"
    assert spec.range_end == "10.0.0.100"
    assert spec.gateway == "10.0.0.1"
    assert spec.dns_servers == ("10.0.0.2",)
    # scope id is derived from the network id so it survives renames.
    assert spec.scope_id == "scope-net_1"

    assert not any(isinstance(s, EnsureDhcpScopeStep) for s in non_edge_steps)


def test_dns_zone_emitted_only_on_edge_node() -> None:
    subnet = Subnet(
        id=SubnetId("sn_1"),
        cidr="10.0.0.0/24",
        dns_zone="prod.lan",
    )
    network = _network(nodes=(NodeId("node_a"), NodeId("node_b")), subnet=subnet)

    edge_steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=[],
        observed=_observed(NodeId("node_a")),
    )
    non_edge_steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_b"),
        peers=[],
        observed=_observed(NodeId("node_b")),
    )

    zone_steps = [s for s in edge_steps if isinstance(s, EnsureDnsZoneStep)]
    assert len(zone_steps) == 1
    assert zone_steps[0].spec.zone == "prod.lan"
    assert not any(isinstance(s, EnsureDnsZoneStep) for s in non_edge_steps)


def test_nat_requires_subnet_and_edge_node() -> None:
    subnet = Subnet(id=SubnetId("sn_1"), cidr="10.0.0.0/24")
    network = _network(
        nodes=(NodeId("node_a"),),
        subnet=subnet,
        nat=NatSpec(egress_interface="eth0"),
    )

    steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=[],
        observed=_observed(NodeId("node_a")),
    )

    nat_steps = [s for s in steps if isinstance(s, EnsureNatRuleStep)]
    assert len(nat_steps) == 1
    assert nat_steps[0].spec.source_cidr == "10.0.0.0/24"
    assert nat_steps[0].spec.egress_interface == "eth0"
    assert nat_steps[0].spec.rule_id == "nat-net_1"


def test_nat_without_subnet_is_skipped() -> None:
    """NAT needs a source CIDR — without a subnet there's nothing to translate."""
    network = _network(
        nodes=(NodeId("node_a"),),
        nat=NatSpec(egress_interface="eth0"),
    )

    steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=[],
        observed=_observed(NodeId("node_a")),
    )

    assert not any(isinstance(s, EnsureNatRuleStep) for s in steps)


def test_firewall_policy_emitted_on_edge_node() -> None:
    fw = FirewallPolicy(
        default_action=FirewallAction.DROP,
        rules=(
            FirewallRule(
                action=FirewallAction.ACCEPT,
                proto=FirewallProto.TCP,
                destination_port_start=443,
                destination_port_end=443,
            ),
        ),
    )
    network = _network(
        nodes=(NodeId("node_a"), NodeId("node_b")),
        firewall=fw,
    )

    edge_steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=[],
        observed=_observed(NodeId("node_a")),
    )
    non_edge_steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_b"),
        peers=[],
        observed=_observed(NodeId("node_b")),
    )

    fw_steps = [s for s in edge_steps if isinstance(s, EnsureFirewallPolicyStep)]
    assert len(fw_steps) == 1
    spec = fw_steps[0].spec
    assert spec.policy_id == "policy-net_1"
    assert spec.default_action == "drop"
    assert len(spec.rules) == 1
    assert spec.rules[0].action == "accept"
    assert spec.rules[0].proto == "tcp"
    assert spec.rules[0].destination_port_start == 443

    assert not any(isinstance(s, EnsureFirewallPolicyStep) for s in non_edge_steps)


def test_network_with_no_edge_intent_emits_no_edge_steps() -> None:
    network = _network(nodes=(NodeId("node_a"),))
    steps = diff_for_node(
        network=network,
        local_node_id=NodeId("node_a"),
        peers=[],
        observed=_observed(NodeId("node_a")),
    )
    for step in steps:
        assert not isinstance(
            step,
            EnsureDhcpScopeStep | EnsureDnsZoneStep | EnsureNatRuleStep | EnsureFirewallPolicyStep,
        )
