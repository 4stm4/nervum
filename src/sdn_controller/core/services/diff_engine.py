"""Diff engine: turn (desired network, observed OVS state) into plan steps.

The engine is **pure** — it gets the desired ``Network``, the local view of
that network's other members, and the observed state of one node, and
returns the list of ``PlanStep`` objects required to make the observed
match the desired. No I/O, no clock, no agent.

Naming policy (stable across renames as long as ``network.name`` doesn't
change):

* bridge name: ``br-<network.name>``
* VXLAN port name: ``vx-<vni>-<short(remote_node_id)>``

Every controller-owned object is tagged with ``external_ids``:

* ``owner=sdn-controller`` — anything *we* care about
* ``network_id=<NetworkId>``
* ``managed_by=sdn-controller`` (port-level)

That tagging is how the controller can later sweep up orphans without
guessing which bridge/port is "ours".

VLAN / flat networks: this engine ensures the bridge exists with the
right tag (M5 minimum). Per-VM port attachment lives in later milestones.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sdn_controller.core.entities import Network, ObservedState
from sdn_controller.core.value_objects.enums import NetworkType
from sdn_controller.core.value_objects.ids import NetworkId, NodeId
from sdn_controller.ports.agent import (
    DeleteBridgeStep,
    DhcpScopeStepSpec,
    DnsZoneStepSpec,
    EnsureBridgeStep,
    EnsureDhcpScopeStep,
    EnsureDnsZoneStep,
    EnsureFirewallPolicyStep,
    EnsureNatRuleStep,
    EnsureVxlanPortStep,
    FirewallPolicyStepSpec,
    FirewallRuleWire,
    NatRuleStepSpec,
    PlanStep,
)

OWNER_LABEL = "sdn-controller"
OWNER_KEY = "owner"
NETWORK_KEY = "network_id"


@dataclass(frozen=True, slots=True)
class NodeAddress:
    """Tunnel endpoint of a node, used by VXLAN diff."""

    node_id: NodeId
    mgmt_ip: str


def bridge_name(network: Network) -> str:
    """Stable OVS bridge name for a network. Operator-readable."""
    return f"br-{network.name}"


def vxlan_port_name(*, vni: int, remote: NodeAddress) -> str:
    """Stable VXLAN port name, short enough for kernel iface limits."""
    return f"vx-{vni}-{remote.node_id[-6:]}"


def dhcp_scope_id(network: Network) -> str:
    """Stable scope id used to identify a DHCP fragment on the agent."""
    return f"scope-{network.id}"


def nat_rule_id(network: Network) -> str:
    return f"nat-{network.id}"


def firewall_policy_id(network: Network) -> str:
    return f"policy-{network.id}"


def is_edge_node(*, network: Network, local_node_id: NodeId) -> bool:
    """Designate exactly one node per network to carry the edge services.

    For M7 we use the first node in ``network.node_ids`` so the pick is
    deterministic. A future Milestone (HA) will replace this with
    role-based selection (e.g. a node labelled ``edge=true``).
    """
    return bool(network.node_ids) and network.node_ids[0] == local_node_id


def diff_for_node(
    *,
    network: Network,
    local_node_id: NodeId,
    peers: Iterable[NodeAddress],
    observed: ObservedState,
) -> list[PlanStep]:
    """Compute the plan steps to bring one node's OVS state into compliance.

    ``peers`` excludes ``local_node_id`` — they are the *other* nodes the
    network spans. For VXLAN, one tunnel port is created per peer.

    The diff is *minimal*: each step is emitted only when the observed state
    materially differs from the desired one. That's what makes "noop produces
    empty plan" hold (SDN-017 acceptance) and lets the reconciler's verify
    phase trust ``is_in_compliance``.
    """
    if local_node_id not in network.node_ids:
        # Node is not part of this network — only thing to do is sweep up any
        # bridge/port we own for this network.
        return _cleanup_for_network(network=network, observed=observed)

    steps: list[PlanStep] = []
    br_name = bridge_name(network)
    desired_bridge_ids = {OWNER_KEY: OWNER_LABEL, NETWORK_KEY: network.id}
    desired_datapath = "system"

    observed_bridge = observed.find_bridge(br_name)
    if observed_bridge is None or _bridge_changed(
        observed_bridge,
        desired_datapath=desired_datapath,
        desired_external_ids=desired_bridge_ids,
    ):
        steps.append(
            EnsureBridgeStep(
                name=br_name,
                datapath_type=desired_datapath,
                external_ids=desired_bridge_ids,
            )
        )

    if network.type is NetworkType.VXLAN and network.vni is not None:
        desired_port_ids = {OWNER_KEY: OWNER_LABEL, NETWORK_KEY: network.id}
        for peer in peers:
            if peer.node_id == local_node_id:
                continue  # never tunnel to ourselves
            port_name = vxlan_port_name(vni=network.vni, remote=peer)
            desired_options = _vxlan_options(network=network, peer=peer)
            observed_port = _find_port(observed_bridge, port_name)
            if observed_port is None or _vxlan_port_changed(
                observed_port,
                desired_options=desired_options,
                desired_external_ids=desired_port_ids,
            ):
                steps.append(
                    EnsureVxlanPortStep(
                        bridge=br_name,
                        name=port_name,
                        vni=network.vni,
                        remote_ip=peer.mgmt_ip,
                        mtu=network.mtu,
                        external_ids=desired_port_ids,
                    )
                )

    # Tear down anything previously owned for this network that no longer fits.
    # In M5 we don't reshape per-port-on-a-VM, so this catches the case where
    # the bridge name changed or the network was renamed.
    for ob_bridge in observed.bridges:
        if ob_bridge.external_ids.get(NETWORK_KEY) != network.id:
            continue
        if ob_bridge.name == br_name:
            continue
        steps.append(DeleteBridgeStep(name=ob_bridge.name))

    # M7: edge services live on a single designated node per network. The
    # agent's idempotent ``apply`` makes re-emitting steps cheap, so we
    # don't compute a fine-grained diff against observed DHCP/DNS/NAT/FW
    # state — observe-vs-desired comparison happens at the agent layer.
    if is_edge_node(network=network, local_node_id=local_node_id):
        steps.extend(_edge_service_steps(network))

    return steps


def _edge_service_steps(network: Network) -> list[PlanStep]:
    """Emit DHCP/DNS/NAT/firewall steps for the network's edge node."""
    out: list[PlanStep] = []

    if network.subnet is not None and network.subnet.dhcp is not None:
        dhcp = network.subnet.dhcp
        out.append(
            EnsureDhcpScopeStep(
                spec=DhcpScopeStepSpec(
                    scope_id=dhcp_scope_id(network),
                    cidr=network.subnet.cidr,
                    range_start=dhcp.range_start,
                    range_end=dhcp.range_end,
                    gateway=network.subnet.gateway,
                    dns_servers=tuple(network.subnet.dns_servers),
                    lease_time_seconds=dhcp.lease_time_seconds,
                    domain_name=dhcp.domain_name,
                ),
            )
        )

    if network.subnet is not None and network.subnet.dns_zone:
        # Records derive from operator-managed allocations in later
        # milestones; M7 ships an empty zone so the agent has somewhere to
        # serve queries.
        out.append(
            EnsureDnsZoneStep(
                spec=DnsZoneStepSpec(zone=network.subnet.dns_zone),
            )
        )

    if network.nat is not None and network.subnet is not None:
        out.append(
            EnsureNatRuleStep(
                spec=NatRuleStepSpec(
                    rule_id=nat_rule_id(network),
                    source_cidr=network.subnet.cidr,
                    egress_interface=network.nat.egress_interface,
                ),
            )
        )

    if network.firewall_policy is not None:
        out.append(
            EnsureFirewallPolicyStep(
                spec=FirewallPolicyStepSpec(
                    policy_id=firewall_policy_id(network),
                    default_action=network.firewall_policy.default_action.value,
                    rules=tuple(
                        FirewallRuleWire(
                            action=r.action.value,
                            proto=r.proto.value,
                            source_cidr=r.source_cidr,
                            destination_cidr=r.destination_cidr,
                            destination_port_start=r.destination_port_start,
                            destination_port_end=r.destination_port_end,
                        )
                        for r in network.firewall_policy.rules
                    ),
                ),
            )
        )

    return out


def _bridge_changed(
    observed_bridge: Any,
    *,
    desired_datapath: str,
    desired_external_ids: dict[str, str],
) -> bool:
    if observed_bridge.datapath_type != desired_datapath:
        return True
    return dict(observed_bridge.external_ids) != desired_external_ids


def _find_port(observed_bridge: Any | None, port_name: str) -> Any | None:
    if observed_bridge is None:
        return None
    for p in observed_bridge.ports:
        if p.name == port_name:
            return p
    return None


def _vxlan_options(*, network: Network, peer: NodeAddress) -> dict[str, str]:
    """The canonical option dict an ``EnsureVxlanPortStep`` would set."""
    opts: dict[str, str] = {
        "dst_port": "4789",
        "key": str(network.vni),
        "remote_ip": peer.mgmt_ip,
    }
    if network.mtu is not None:
        opts["mtu_request"] = str(network.mtu)
    return opts


def _vxlan_port_changed(
    observed_port: Any,
    *,
    desired_options: dict[str, str],
    desired_external_ids: dict[str, str],
) -> bool:
    if dict(observed_port.external_ids) != desired_external_ids:
        return True
    if not observed_port.interfaces:
        return True
    iface = observed_port.interfaces[0]
    if iface.type != "vxlan":
        return True
    # Compare option-by-option so an OVS-added option (we don't write) doesn't
    # trigger spurious "changed". The agent's adapter enforces what *we* set.
    observed_options = dict(iface.options)
    return any(observed_options.get(k) != v for k, v in desired_options.items())


def _cleanup_for_network(*, network: Network, observed: ObservedState) -> list[PlanStep]:
    """A node was removed from the network — sweep its bridges for this id."""
    return [
        DeleteBridgeStep(name=b.name)
        for b in observed.bridges
        if b.external_ids.get(NETWORK_KEY) == network.id
    ]


_EDGE_STEP_TYPES: tuple[type, ...] = (
    EnsureDhcpScopeStep,
    EnsureDnsZoneStep,
    EnsureNatRuleStep,
    EnsureFirewallPolicyStep,
)


def is_in_compliance(
    *,
    network: Network,
    local_node_id: NodeId,
    peers: Iterable[NodeAddress],
    observed: ObservedState,
) -> bool:
    """True iff there's no *structural* (OVS) drift on this node.

    Edge-service steps (DHCP/DNS/NAT/firewall) are emitted unconditionally
    on the edge node because the controller doesn't observe edge-service
    state directly — the agent owns idempotency for those. Compliance is
    therefore an OVS-level check; the reconciler trusts the per-step
    ``ok`` flag from ``apply_plan`` for edge services.
    """
    steps = diff_for_node(
        network=network,
        local_node_id=local_node_id,
        peers=peers,
        observed=observed,
    )
    return not any(not isinstance(s, _EDGE_STEP_TYPES) for s in steps)


__all__ = [
    "NETWORK_KEY",
    "OWNER_KEY",
    "OWNER_LABEL",
    "NodeAddress",
    "bridge_name",
    "diff_for_node",
    "is_in_compliance",
    "vxlan_port_name",
]


# Help linters: NetworkId is used in dataclass annotations.
_ = NetworkId
