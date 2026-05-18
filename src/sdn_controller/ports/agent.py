"""Southbound agent port.

The core depends on this protocol, not on a particular transport. The
``HttpAgentClient`` adapter (``sdn_controller.adapters.netos_agent``) is the
production implementation; tests substitute a fake.

We deliberately **mirror** the agent's plan model here instead of importing
from ``netos_agent``: the two packages stay independently deployable, the
import graph stays acyclic, and a contract test in the adapter guards against
schema drift. ``NodeCapabilities`` *is* shared because it's a core domain
value object (lives in ``core.value_objects.capabilities``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.ids import NodeId

__all__ = [
    "AgentPort",
    "DeleteBridgeStep",
    "DeleteDhcpScopeStep",
    "DeleteDnsZoneStep",
    "DeleteFirewallPolicyStep",
    "DeleteNatRuleStep",
    "DeletePortStep",
    "DhcpScopeStepSpec",
    "DnsRecordWire",
    "DnsZoneStepSpec",
    "EnsureBridgeStep",
    "EnsureDhcpScopeStep",
    "EnsureDnsZoneStep",
    "EnsureFirewallPolicyStep",
    "EnsureNatRuleStep",
    "EnsurePortStep",
    "EnsureVxlanPortStep",
    "FirewallPolicyStepSpec",
    "FirewallRuleWire",
    "NatRuleStepSpec",
    "NodeCapabilities",
    "OvsBridgeView",
    "OvsInterfaceView",
    "OvsPortView",
    "OvsStateView",
    "Plan",
    "PlanResult",
    "PlanStep",
    "PlanStepResult",
    "SnapshotRef",
]


# ---------------------------------------------------------------------------
# Plan model (mirror of netos_agent.core.value_objects.plan)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnsureBridgeStep:
    name: str
    datapath_type: str = "system"
    external_ids: dict[str, str] = field(default_factory=dict)
    action: Literal["ensure_bridge"] = "ensure_bridge"


@dataclass(frozen=True, slots=True)
class DeleteBridgeStep:
    name: str
    action: Literal["delete_bridge"] = "delete_bridge"


@dataclass(frozen=True, slots=True)
class EnsurePortStep:
    bridge: str
    name: str
    type: str = "internal"
    options: dict[str, str] = field(default_factory=dict)
    tag: int | None = None  # access VLAN
    trunks: tuple[int, ...] = ()  # trunked VLANs
    external_ids: dict[str, str] = field(default_factory=dict)
    action: Literal["ensure_port"] = "ensure_port"


@dataclass(frozen=True, slots=True)
class DeletePortStep:
    bridge: str
    name: str
    action: Literal["delete_port"] = "delete_port"


@dataclass(frozen=True, slots=True)
class EnsureVxlanPortStep:
    bridge: str
    name: str
    vni: int
    remote_ip: str
    local_ip: str | None = None
    dst_port: int = 4789
    mtu: int | None = None
    external_ids: dict[str, str] = field(default_factory=dict)
    action: Literal["ensure_vxlan_port"] = "ensure_vxlan_port"


# ---------------------------------------------------------------------------
# Edge-service step specs (M7) — wire mirror of netos_agent's value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DhcpScopeStepSpec:
    scope_id: str
    cidr: str
    range_start: str
    range_end: str
    gateway: str | None = None
    dns_servers: tuple[str, ...] = ()
    lease_time_seconds: int = 3600
    domain_name: str | None = None


@dataclass(frozen=True, slots=True)
class DnsRecordWire:
    name: str
    type: Literal["A", "AAAA", "CNAME"]
    value: str
    ttl_seconds: int = 300


@dataclass(frozen=True, slots=True)
class DnsZoneStepSpec:
    zone: str
    records: tuple[DnsRecordWire, ...] = ()
    soa_email: str = "hostmaster.invalid."


@dataclass(frozen=True, slots=True)
class NatRuleStepSpec:
    rule_id: str
    source_cidr: str
    egress_interface: str


@dataclass(frozen=True, slots=True)
class FirewallRuleWire:
    action: Literal["accept", "drop"] = "accept"
    proto: Literal["any", "tcp", "udp", "icmp"] = "any"
    source_cidr: str | None = None
    destination_cidr: str | None = None
    destination_port_start: int | None = None
    destination_port_end: int | None = None


@dataclass(frozen=True, slots=True)
class FirewallPolicyStepSpec:
    policy_id: str
    default_action: Literal["accept", "drop"] = "drop"
    rules: tuple[FirewallRuleWire, ...] = ()


@dataclass(frozen=True, slots=True)
class EnsureDhcpScopeStep:
    spec: DhcpScopeStepSpec
    action: Literal["ensure_dhcp_scope"] = "ensure_dhcp_scope"


@dataclass(frozen=True, slots=True)
class DeleteDhcpScopeStep:
    scope_id: str
    action: Literal["delete_dhcp_scope"] = "delete_dhcp_scope"


@dataclass(frozen=True, slots=True)
class EnsureDnsZoneStep:
    spec: DnsZoneStepSpec
    action: Literal["ensure_dns_zone"] = "ensure_dns_zone"


@dataclass(frozen=True, slots=True)
class DeleteDnsZoneStep:
    zone: str
    action: Literal["delete_dns_zone"] = "delete_dns_zone"


@dataclass(frozen=True, slots=True)
class EnsureNatRuleStep:
    spec: NatRuleStepSpec
    action: Literal["ensure_nat_rule"] = "ensure_nat_rule"


@dataclass(frozen=True, slots=True)
class DeleteNatRuleStep:
    rule_id: str
    action: Literal["delete_nat_rule"] = "delete_nat_rule"


@dataclass(frozen=True, slots=True)
class EnsureFirewallPolicyStep:
    spec: FirewallPolicyStepSpec
    action: Literal["ensure_firewall_policy"] = "ensure_firewall_policy"


@dataclass(frozen=True, slots=True)
class DeleteFirewallPolicyStep:
    policy_id: str
    action: Literal["delete_firewall_policy"] = "delete_firewall_policy"


PlanStep = (
    EnsureBridgeStep
    | DeleteBridgeStep
    | EnsurePortStep
    | DeletePortStep
    | EnsureVxlanPortStep
    | EnsureDhcpScopeStep
    | DeleteDhcpScopeStep
    | EnsureDnsZoneStep
    | DeleteDnsZoneStep
    | EnsureNatRuleStep
    | DeleteNatRuleStep
    | EnsureFirewallPolicyStep
    | DeleteFirewallPolicyStep
)


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    steps: tuple[PlanStep, ...]


@dataclass(frozen=True, slots=True)
class PlanStepResult:
    action: str
    ok: bool
    changed: bool = False
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanResult:
    plan_id: str
    ok: bool
    steps: tuple[PlanStepResult, ...] = ()


# ---------------------------------------------------------------------------
# OVS state view (read-only projection the agent returns)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OvsInterfaceView:
    name: str
    type: str
    options: dict[str, str] = field(default_factory=dict)
    admin_state: str = "up"


@dataclass(frozen=True, slots=True)
class OvsPortView:
    name: str
    interfaces: tuple[OvsInterfaceView, ...] = ()
    tag: int | None = None
    trunks: tuple[int, ...] = ()
    external_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OvsBridgeView:
    name: str
    datapath_type: str = "system"
    ports: tuple[OvsPortView, ...] = ()
    external_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OvsStateView:
    ovs_version: str | None = None
    bridges: tuple[OvsBridgeView, ...] = ()
    state_hash: str = ""

    def find_bridge(self, name: str) -> OvsBridgeView | None:
        for b in self.bridges:
            if b.name == name:
                return b
        return None


# ---------------------------------------------------------------------------
# Snapshot reference returned to the controller
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    id: str
    state_hash: str
    created_at: datetime
    label: str | None = None


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


class AgentPort(Protocol):
    """Everything the controller asks an agent to do.

    Implementations:

    * ``HttpAgentClient`` — production, talks to ``netos_agent`` over HTTPS
      (M9 adds mTLS).
    * In-test fakes that satisfy the protocol without a network round-trip.
    """

    async def get_capabilities(self, node_id: NodeId) -> NodeCapabilities: ...

    async def get_state(self, node_id: NodeId) -> OvsStateView: ...

    async def apply_plan(self, node_id: NodeId, plan: Plan) -> PlanResult: ...

    async def snapshot(self, node_id: NodeId, *, label: str | None = None) -> SnapshotRef: ...

    async def restore(self, node_id: NodeId, snapshot_id: str) -> SnapshotRef: ...
