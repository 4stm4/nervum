"""Wire DTOs for the agent's HTTP API.

Dedicated Pydantic layer means:

* the contract is reviewable in OpenAPI;
* core dataclasses don't leak through FastAPI's default serializer;
* the discriminated plan-step union gets validated by Pydantic before any
  use case sees it (so the controller learns about malformed JSON as 422,
  not 500).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from netos_agent.core.entities import (
    BridgeState,
    InterfaceState,
    OvsSnapshot,
    OvsState,
    PortState,
)
from netos_agent.core.use_cases.get_state import NodeState
from netos_agent.core.value_objects.edge_services import (
    DhcpScopeSpec,
    DnsRecord,
    DnsZoneSpec,
    FirewallAction,
    FirewallPolicySpec,
    FirewallProto,
    FirewallRuleSpec,
    NatRuleSpec,
)
from netos_agent.core.value_objects.plan import (
    DeleteBridgeStep,
    DeleteDhcpScopeStep,
    DeleteDnsZoneStep,
    DeleteFirewallPolicyStep,
    DeleteNatRuleStep,
    DeletePortStep,
    EnsureBridgeStep,
    EnsureDhcpScopeStep,
    EnsureDnsZoneStep,
    EnsureFirewallPolicyStep,
    EnsureNatRuleStep,
    EnsurePortStep,
    EnsureVxlanPortStep,
    Plan,
    PlanResult,
    PlanStep,
    PlanStepResult,
)
from netos_agent.core.value_objects.system_info import SystemInfo, SystemStats

# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ok", "not_ready"]
    ovs_version: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# OVS state
# ---------------------------------------------------------------------------


class InterfaceOut(BaseModel):
    name: str
    type: str
    options: dict[str, str]
    admin_state: str

    @classmethod
    def from_domain(cls, iface: InterfaceState) -> InterfaceOut:
        return cls(
            name=iface.name,
            type=iface.type,
            options=dict(iface.options),
            admin_state=iface.admin_state,
        )


class PortOut(BaseModel):
    name: str
    tag: int | None
    trunks: list[int]
    external_ids: dict[str, str] = Field(default_factory=dict)
    interfaces: list[InterfaceOut]

    @classmethod
    def from_domain(cls, port: PortState) -> PortOut:
        return cls(
            name=port.name,
            tag=port.tag,
            trunks=list(port.trunks),
            external_ids=dict(port.external_ids),
            interfaces=[InterfaceOut.from_domain(i) for i in port.interfaces],
        )


class BridgeOut(BaseModel):
    name: str
    datapath_type: str
    external_ids: dict[str, str] = Field(default_factory=dict)
    ports: list[PortOut]

    @classmethod
    def from_domain(cls, bridge: BridgeState) -> BridgeOut:
        return cls(
            name=bridge.name,
            datapath_type=bridge.datapath_type,
            external_ids=dict(bridge.external_ids),
            ports=[PortOut.from_domain(p) for p in bridge.ports],
        )


class OvsStateOut(BaseModel):
    ovs_version: str | None
    bridges: list[BridgeOut]
    state_hash: str

    @classmethod
    def from_domain(cls, state: OvsState) -> OvsStateOut:
        return cls(
            ovs_version=state.ovs_version,
            bridges=[BridgeOut.from_domain(b) for b in state.bridges],
            state_hash=state.hash,
        )


# ---------------------------------------------------------------------------
# Node info / system stats
# ---------------------------------------------------------------------------


class SystemInfoOut(BaseModel):
    hostname: str
    kernel: str | None
    cpu_count: int | None
    memory_total_bytes: int | None

    @classmethod
    def from_domain(cls, info: SystemInfo) -> SystemInfoOut:
        return cls(
            hostname=info.hostname,
            kernel=info.kernel,
            cpu_count=info.cpu_count,
            memory_total_bytes=info.memory_total_bytes,
        )


class SystemStatsOut(BaseModel):
    uptime_seconds: float | None
    load_avg_1m: float | None
    load_avg_5m: float | None
    load_avg_15m: float | None
    memory_used_bytes: int | None

    @classmethod
    def from_domain(cls, s: SystemStats) -> SystemStatsOut:
        return cls(
            uptime_seconds=s.uptime_seconds,
            load_avg_1m=s.load_avg_1m,
            load_avg_5m=s.load_avg_5m,
            load_avg_15m=s.load_avg_15m,
            memory_used_bytes=s.memory_used_bytes,
        )


class NodeStateOut(BaseModel):
    info: SystemInfoOut
    ovs_state: OvsStateOut

    @classmethod
    def from_domain(cls, state: NodeState) -> NodeStateOut:
        return cls(
            info=SystemInfoOut.from_domain(state.info),
            ovs_state=OvsStateOut.from_domain(state.ovs_state),
        )


# ---------------------------------------------------------------------------
# Plan request / result
# ---------------------------------------------------------------------------


class EnsureBridgeStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["ensure_bridge"]
    name: str = Field(min_length=1)
    datapath_type: str = "system"
    external_ids: dict[str, str] = Field(default_factory=dict)


class DeleteBridgeStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete_bridge"]
    name: str = Field(min_length=1)


class EnsurePortStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["ensure_port"]
    bridge: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = "internal"
    options: dict[str, str] = Field(default_factory=dict)
    tag: int | None = Field(default=None, ge=1, le=4094)
    trunks: list[int] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)


class DeletePortStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete_port"]
    bridge: str = Field(min_length=1)
    name: str = Field(min_length=1)


class EnsureVxlanPortStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["ensure_vxlan_port"]
    bridge: str = Field(min_length=1)
    name: str = Field(min_length=1)
    vni: int = Field(ge=1, le=16_777_215)
    remote_ip: str = Field(min_length=1)
    local_ip: str | None = None
    dst_port: int = Field(default=4789, ge=1, le=65535)
    mtu: int | None = Field(default=None, ge=576, le=9216)
    external_ids: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Edge-service step DTOs (M7)
# ---------------------------------------------------------------------------


class DhcpScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_id: str = Field(min_length=1, max_length=128)
    cidr: str = Field(min_length=1)
    range_start: str = Field(min_length=1)
    range_end: str = Field(min_length=1)
    gateway: str | None = None
    dns_servers: list[str] = Field(default_factory=list)
    lease_time_seconds: int = Field(default=3600, ge=60, le=86_400)
    domain_name: str | None = None


class EnsureDhcpScopeStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["ensure_dhcp_scope"]
    spec: DhcpScopeIn


class DeleteDhcpScopeStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete_dhcp_scope"]
    scope_id: str = Field(min_length=1)


class DnsRecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: Literal["A", "AAAA", "CNAME"]
    value: str = Field(min_length=1)
    ttl_seconds: int = Field(default=300, ge=1, le=86_400)


class DnsZoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone: str = Field(min_length=1)
    records: list[DnsRecordIn] = Field(default_factory=list)
    soa_email: str = "hostmaster.invalid."


class EnsureDnsZoneStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["ensure_dns_zone"]
    spec: DnsZoneIn


class DeleteDnsZoneStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete_dns_zone"]
    zone: str = Field(min_length=1)


class NatRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=128)
    source_cidr: str = Field(min_length=1)
    egress_interface: str = Field(min_length=1, max_length=64)


class EnsureNatRuleStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["ensure_nat_rule"]
    spec: NatRuleIn


class DeleteNatRuleStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete_nat_rule"]
    rule_id: str = Field(min_length=1)


class FirewallRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "drop"] = "accept"
    proto: Literal["any", "tcp", "udp", "icmp"] = "any"
    source_cidr: str | None = None
    destination_cidr: str | None = None
    destination_port_start: int | None = Field(default=None, ge=1, le=65535)
    destination_port_end: int | None = Field(default=None, ge=1, le=65535)


class FirewallPolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, max_length=128)
    default_action: Literal["accept", "drop"] = "drop"
    rules: list[FirewallRuleIn] = Field(default_factory=list)


class EnsureFirewallPolicyStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["ensure_firewall_policy"]
    spec: FirewallPolicyIn


class DeleteFirewallPolicyStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete_firewall_policy"]
    policy_id: str = Field(min_length=1)


PlanStepIn = Annotated[
    EnsureBridgeStepIn
    | DeleteBridgeStepIn
    | EnsurePortStepIn
    | DeletePortStepIn
    | EnsureVxlanPortStepIn
    | EnsureDhcpScopeStepIn
    | DeleteDhcpScopeStepIn
    | EnsureDnsZoneStepIn
    | DeleteDnsZoneStepIn
    | EnsureNatRuleStepIn
    | DeleteNatRuleStepIn
    | EnsureFirewallPolicyStepIn
    | DeleteFirewallPolicyStepIn,
    Field(discriminator="action"),
]


class PlanApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=128)
    steps: list[PlanStepIn] = Field(min_length=1)

    def to_domain(self) -> Plan:
        return Plan(plan_id=self.plan_id, steps=tuple(_step_to_domain(s) for s in self.steps))


def _step_to_domain(step: PlanStepIn) -> PlanStep:  # noqa: PLR0911, PLR0912 — discriminated dispatch
    match step:
        case EnsureBridgeStepIn():
            return EnsureBridgeStep(
                name=step.name,
                datapath_type=step.datapath_type,
                external_ids=dict(step.external_ids),
            )
        case DeleteBridgeStepIn():
            return DeleteBridgeStep(name=step.name)
        case EnsurePortStepIn():
            return EnsurePortStep(
                bridge=step.bridge,
                name=step.name,
                type=step.type,
                options=dict(step.options),
                tag=step.tag,
                trunks=tuple(step.trunks),
                external_ids=dict(step.external_ids),
            )
        case DeletePortStepIn():
            return DeletePortStep(bridge=step.bridge, name=step.name)
        case EnsureVxlanPortStepIn():
            return EnsureVxlanPortStep(
                bridge=step.bridge,
                name=step.name,
                vni=step.vni,
                remote_ip=step.remote_ip,
                local_ip=step.local_ip,
                dst_port=step.dst_port,
                mtu=step.mtu,
                external_ids=dict(step.external_ids),
            )
        case EnsureDhcpScopeStepIn():
            return EnsureDhcpScopeStep(spec=_dhcp_scope_to_domain(step.spec))
        case DeleteDhcpScopeStepIn():
            return DeleteDhcpScopeStep(scope_id=step.scope_id)
        case EnsureDnsZoneStepIn():
            return EnsureDnsZoneStep(spec=_dns_zone_to_domain(step.spec))
        case DeleteDnsZoneStepIn():
            return DeleteDnsZoneStep(zone=step.zone)
        case EnsureNatRuleStepIn():
            return EnsureNatRuleStep(spec=_nat_rule_to_domain(step.spec))
        case DeleteNatRuleStepIn():
            return DeleteNatRuleStep(rule_id=step.rule_id)
        case EnsureFirewallPolicyStepIn():
            return EnsureFirewallPolicyStep(spec=_firewall_policy_to_domain(step.spec))
        case DeleteFirewallPolicyStepIn():
            return DeleteFirewallPolicyStep(policy_id=step.policy_id)


def _dhcp_scope_to_domain(io: DhcpScopeIn) -> DhcpScopeSpec:
    return DhcpScopeSpec(
        scope_id=io.scope_id,
        cidr=io.cidr,
        range_start=io.range_start,
        range_end=io.range_end,
        gateway=io.gateway,
        dns_servers=tuple(io.dns_servers),
        lease_time_seconds=io.lease_time_seconds,
        domain_name=io.domain_name,
    )


def _dns_zone_to_domain(io: DnsZoneIn) -> DnsZoneSpec:
    return DnsZoneSpec(
        zone=io.zone,
        records=tuple(
            DnsRecord(name=r.name, type=r.type, value=r.value, ttl_seconds=r.ttl_seconds)
            for r in io.records
        ),
        soa_email=io.soa_email,
    )


def _nat_rule_to_domain(io: NatRuleIn) -> NatRuleSpec:
    return NatRuleSpec(
        rule_id=io.rule_id,
        source_cidr=io.source_cidr,
        egress_interface=io.egress_interface,
    )


def _firewall_policy_to_domain(io: FirewallPolicyIn) -> FirewallPolicySpec:
    return FirewallPolicySpec(
        policy_id=io.policy_id,
        default_action=FirewallAction(io.default_action),
        rules=tuple(
            FirewallRuleSpec(
                action=FirewallAction(r.action),
                proto=FirewallProto(r.proto),
                source_cidr=r.source_cidr,
                destination_cidr=r.destination_cidr,
                destination_port_start=r.destination_port_start,
                destination_port_end=r.destination_port_end,
            )
            for r in io.rules
        ),
    )


class PlanStepResultOut(BaseModel):
    action: str
    ok: bool
    changed: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, r: PlanStepResult) -> PlanStepResultOut:
        return cls(
            action=r.action,
            ok=r.ok,
            changed=r.changed,
            message=r.message,
            details=dict(r.details),
        )


class PlanResultOut(BaseModel):
    plan_id: str
    ok: bool
    steps: list[PlanStepResultOut]

    @classmethod
    def from_domain(cls, r: PlanResult) -> PlanResultOut:
        return cls(
            plan_id=r.plan_id,
            ok=r.ok,
            steps=[PlanStepResultOut.from_domain(s) for s in r.steps],
        )


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


class SnapshotCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=255)


class SnapshotOut(BaseModel):
    id: str
    created_at: datetime
    state_hash: str
    label: str | None

    @classmethod
    def from_domain(cls, s: OvsSnapshot) -> SnapshotOut:
        return cls(
            id=s.id,
            created_at=s.created_at,
            state_hash=s.state_hash,
            label=s.label,
        )


class SnapshotListResponse(BaseModel):
    items: list[SnapshotOut]


class SnapshotRestoreResponse(BaseModel):
    snapshot: SnapshotOut
    ovs_state: OvsStateOut
