"""Wire-format DTOs.

We *never* let FastAPI serialize domain entities directly. A dedicated
Pydantic layer means:

* the OpenAPI contract is explicit and reviewable,
* internal refactors don't accidentally leak into the API,
* validation errors come from Pydantic, not from a deep ``__post_init__``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sdn_controller.core.entities import (
    DriftItem,
    DriftReport,
    Network,
    Node,
    Operation,
    OperationEvent,
    Subnet,
    Topology,
    TopologyBridge,
    TopologyEdge,
    TopologyNetwork,
    TopologyNode,
)
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.edge_services import (
    DhcpSpec,
    FirewallPolicy,
    FirewallRule,
    NatSpec,
)
from sdn_controller.core.value_objects.enums import (
    NetworkType,
    NodeStatus,
    OperationKind,
    OperationStatus,
)

# ---------------------------------------------------------------------------
# Common envelopes
# ---------------------------------------------------------------------------


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class OperationLinks(BaseModel):
    self: str
    events: str


class OperationEnvelope(BaseModel):
    """Returned by every mutating endpoint."""

    operation_id: str
    status: OperationStatus
    resource: dict[str, str]
    links: OperationLinks


# ---------------------------------------------------------------------------
# Health / version
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class VersionResponse(BaseModel):
    version: str
    api_version: str = "v1"


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


class SubnetIn(BaseModel):
    cidr: str
    gateway: str | None = None


class SubnetOut(BaseModel):
    id: str
    cidr: str
    gateway: str | None = None
    dns_zone: str | None = None

    @classmethod
    def from_domain(cls, sub: Subnet) -> SubnetOut:
        return cls(id=sub.id, cidr=sub.cidr, gateway=sub.gateway, dns_zone=sub.dns_zone)


class NetworkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    type: NetworkType
    mtu: int = Field(default=1500, ge=576, le=9216)
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    vni: int | None = Field(default=None, ge=1, le=16_777_215)
    subnet: SubnetIn | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    node_ids: list[str] = Field(default_factory=list)


class DhcpSpecIO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    range_start: str = Field(min_length=1)
    range_end: str = Field(min_length=1)
    lease_time_seconds: int = Field(default=3600, ge=60, le=86_400)
    domain_name: str | None = None

    @classmethod
    def from_domain(cls, dhcp: DhcpSpec) -> DhcpSpecIO:
        return cls(
            range_start=dhcp.range_start,
            range_end=dhcp.range_end,
            lease_time_seconds=dhcp.lease_time_seconds,
            domain_name=dhcp.domain_name,
        )


class NatSpecIO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    egress_interface: str = Field(min_length=1, max_length=64)

    @classmethod
    def from_domain(cls, nat: NatSpec) -> NatSpecIO:
        return cls(egress_interface=nat.egress_interface)


class FirewallRuleIO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "drop"] = "accept"
    proto: Literal["any", "tcp", "udp", "icmp"] = "any"
    source_cidr: str | None = None
    destination_cidr: str | None = None
    destination_port_start: int | None = Field(default=None, ge=1, le=65535)
    destination_port_end: int | None = Field(default=None, ge=1, le=65535)

    @classmethod
    def from_domain(cls, rule: FirewallRule) -> FirewallRuleIO:
        return cls(
            action=rule.action.value,
            proto=rule.proto.value,
            source_cidr=rule.source_cidr,
            destination_cidr=rule.destination_cidr,
            destination_port_start=rule.destination_port_start,
            destination_port_end=rule.destination_port_end,
        )


class FirewallPolicyIO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_action: Literal["accept", "drop"] = "drop"
    rules: list[FirewallRuleIO] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, fw: FirewallPolicy) -> FirewallPolicyIO:
        return cls(
            default_action=fw.default_action.value,
            rules=[FirewallRuleIO.from_domain(r) for r in fw.rules],
        )


class NetworkUpdateRequest(BaseModel):
    """PATCH body — fields left out are not changed."""

    model_config = ConfigDict(extra="forbid")

    mtu: int | None = Field(default=None, ge=576, le=9216)
    subnet: SubnetIn | None = None
    labels: dict[str, str] | None = None
    nat: NatSpecIO | None = None
    firewall_policy: FirewallPolicyIO | None = None


class NetworkAssignNodesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: list[str] = Field(default_factory=list)


class NetworkOut(BaseModel):
    id: str
    name: str
    type: NetworkType
    mtu: int
    vlan_id: int | None
    vni: int | None
    subnet: SubnetOut | None
    labels: dict[str, str]
    intent_version: int
    spec_hash: str
    node_ids: list[str]
    nat: NatSpecIO | None = None
    firewall_policy: FirewallPolicyIO | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, net: Network) -> NetworkOut:
        return cls(
            id=net.id,
            name=net.name,
            type=net.type,
            mtu=net.mtu,
            vlan_id=net.vlan_id,
            vni=net.vni,
            subnet=SubnetOut.from_domain(net.subnet) if net.subnet is not None else None,
            labels=dict(net.labels),
            intent_version=net.intent_version,
            spec_hash=net.spec_hash,
            node_ids=list(net.node_ids),
            nat=NatSpecIO.from_domain(net.nat) if net.nat is not None else None,
            firewall_policy=(
                FirewallPolicyIO.from_domain(net.firewall_policy)
                if net.firewall_policy is not None
                else None
            ),
            created_at=net.created_at,
            updated_at=net.updated_at,
        )


class NetworkListResponse(BaseModel):
    items: list[NetworkOut]


class NetworkCreateResponse(BaseModel):
    """Combined envelope: the resource ``and`` the operation that produced it."""

    network: NetworkOut
    operation: OperationEnvelope


class NetworkUpdateResponse(BaseModel):
    network: NetworkOut
    operation: OperationEnvelope


class NetworkApplyResponse(BaseModel):
    network: NetworkOut
    operation: OperationEnvelope


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


class NodeCapabilitiesIO(BaseModel):
    """Wire shape for ``NodeCapabilities`` — both request (enroll/heartbeat)
    and response (node read) use the same DTO so the agent doesn't need to
    learn two formats."""

    ovs_version: str | None = None
    kernel: str | None = None
    interfaces: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, caps: NodeCapabilities) -> NodeCapabilitiesIO:
        return cls(
            ovs_version=caps.ovs_version,
            kernel=caps.kernel,
            interfaces=list(caps.interfaces),
            features=list(caps.features),
        )

    def to_domain(self) -> NodeCapabilities:
        return NodeCapabilities(
            ovs_version=self.ovs_version,
            kernel=self.kernel,
            interfaces=tuple(self.interfaces),
            features=tuple(self.features),
        )


class NodeOut(BaseModel):
    id: str
    name: str
    mgmt_ip: str
    status: NodeStatus
    roles: list[str]
    labels: dict[str, str]
    agent_version: str | None
    last_seen_at: datetime | None
    capabilities: NodeCapabilitiesIO | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, node: Node) -> NodeOut:
        return cls(
            id=node.id,
            name=node.name,
            mgmt_ip=node.mgmt_ip,
            status=node.status,
            roles=list(node.roles),
            labels=dict(node.labels),
            agent_version=node.agent_version,
            last_seen_at=node.last_seen_at,
            capabilities=(
                NodeCapabilitiesIO.from_domain(node.capabilities)
                if node.capabilities is not None
                else None
            ),
            created_at=node.created_at,
            updated_at=node.updated_at,
        )


class NodeRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    mgmt_ip: str = Field(min_length=1, max_length=64)
    roles: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class NodeRegisterResponse(BaseModel):
    node: NodeOut
    operation: OperationEnvelope


class EnrollmentTokenIssueResponse(BaseModel):
    """Operator response after issuing an enrolment token.

    ``token`` is the plaintext to hand to the agent; it is never exposed
    again. ``expires_at`` lets the operator see how long they have to
    transfer it.
    """

    token: str
    token_id: str
    node_id: str
    expires_at: datetime
    issued_at: datetime


class AgentEnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    agent_version: str | None = None
    capabilities: NodeCapabilitiesIO | None = None


class AgentEnrollResponse(BaseModel):
    node: NodeOut


class AgentHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    agent_version: str | None = None
    capabilities: NodeCapabilitiesIO | None = None


class AgentHeartbeatResponse(BaseModel):
    node: NodeOut


class NodeListResponse(BaseModel):
    items: list[NodeOut]


# ---------------------------------------------------------------------------
# Operation
# ---------------------------------------------------------------------------


class OperationEventOut(BaseModel):
    sequence: int
    at: datetime
    status: OperationStatus
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, evt: OperationEvent) -> OperationEventOut:
        return cls(
            sequence=evt.sequence,
            at=evt.at,
            status=evt.status,
            message=evt.message,
            payload=dict(evt.payload),
        )


class OperationErrorOut(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class OperationOut(BaseModel):
    id: str
    kind: OperationKind
    status: OperationStatus
    resource: dict[str, str]
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    events: list[OperationEventOut]
    error: OperationErrorOut | None

    @classmethod
    def from_domain(cls, op: Operation) -> OperationOut:
        return cls(
            id=op.id,
            kind=op.kind,
            status=op.status,
            resource={"type": op.resource.type, "id": op.resource.id},
            created_at=op.created_at,
            updated_at=op.updated_at,
            created_by=op.created_by,
            events=[OperationEventOut.from_domain(e) for e in op.events],
            error=(
                OperationErrorOut(
                    code=op.error.code,
                    message=op.error.message,
                    details=dict(op.error.details),
                )
                if op.error is not None
                else None
            ),
        )


class OperationListResponse(BaseModel):
    items: list[OperationOut]


class OperationEventsResponse(BaseModel):
    items: list[OperationEventOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def operation_envelope(op: Operation) -> OperationEnvelope:
    """Build the small operation envelope returned by mutating endpoints."""
    return OperationEnvelope(
        operation_id=op.id,
        status=op.status,
        resource={"type": op.resource.type, "id": op.resource.id},
        links=OperationLinks(
            self=f"/api/v1/operations/{op.id}",
            events=f"/api/v1/operations/{op.id}/events",
        ),
    )


# ---------------------------------------------------------------------------
# IPAM (M6)
# ---------------------------------------------------------------------------


class IpRangeIO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str = Field(min_length=1)
    end: str = Field(min_length=1)


class SubnetUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cidr: str = Field(min_length=1)
    gateway: str | None = None
    dns_servers: list[str] = Field(default_factory=list)
    allocation_pools: list[IpRangeIO] = Field(default_factory=list)
    reserved_ranges: list[IpRangeIO] = Field(default_factory=list)
    dhcp: DhcpSpecIO | None = None
    dns_zone: str | None = Field(default=None, max_length=253)


class SubnetOutFull(BaseModel):
    """Richer subnet DTO used by the IPAM endpoints (the embedded ``SubnetOut``
    in network responses keeps the slimmer shape for backward compat)."""

    id: str
    network_id: str
    cidr: str
    gateway: str | None
    dns_servers: list[str]
    allocation_pools: list[IpRangeIO]
    reserved_ranges: list[IpRangeIO]
    dhcp: DhcpSpecIO | None = None
    dns_zone: str | None = None


class SubnetListResponse(BaseModel):
    items: list[SubnetOutFull]


class OwnerRefIO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=128)


class AllocateIpRequest(BaseModel):
    """Body for ``POST /subnets/{id}/allocations``.

    ``kind`` controls behaviour:
    * ``dynamic`` — controller picks the next free IP (``ip_address`` ignored)
    * ``reservation`` — caller pins ``ip_address`` (must be supplied)
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["dynamic", "reservation"] = "dynamic"
    owner: OwnerRefIO
    ip_address: str | None = None
    label: str | None = Field(default=None, max_length=255)


class IpAllocationOut(BaseModel):
    id: str
    subnet_id: str
    ip_address: str
    owner: OwnerRefIO
    kind: Literal["dynamic", "reservation"]
    allocated_at: datetime
    label: str | None


class IpAllocationListResponse(BaseModel):
    items: list[IpAllocationOut]


# ---------------------------------------------------------------------------
# Топология (M8)
# ---------------------------------------------------------------------------


class TopologyNodeOut(BaseModel):
    id: str
    name: str
    mgmt_ip: str
    status: NodeStatus
    roles: list[str]
    labels: dict[str, str]
    last_seen_at: datetime | None
    observed_state_hash: str | None
    observed_at: datetime | None

    @classmethod
    def from_domain(cls, n: TopologyNode) -> TopologyNodeOut:
        return cls(
            id=n.id,
            name=n.name,
            mgmt_ip=n.mgmt_ip,
            status=n.status,
            roles=list(n.roles),
            labels=dict(n.labels),
            last_seen_at=n.last_seen_at,
            observed_state_hash=n.observed_state_hash,
            observed_at=n.observed_at,
        )


class TopologyNetworkOut(BaseModel):
    id: str
    name: str
    type: NetworkType
    mtu: int
    vlan_id: int | None
    vni: int | None
    subnet: SubnetOut | None
    node_ids: list[str]
    intent_version: int
    spec_hash: str

    @classmethod
    def from_domain(cls, n: TopologyNetwork) -> TopologyNetworkOut:
        return cls(
            id=n.id,
            name=n.name,
            type=n.type,
            mtu=n.mtu,
            vlan_id=n.vlan_id,
            vni=n.vni,
            subnet=SubnetOut.from_domain(n.subnet) if n.subnet is not None else None,
            node_ids=list(n.node_ids),
            intent_version=n.intent_version,
            spec_hash=n.spec_hash,
        )


class TopologyBridgeOut(BaseModel):
    node_id: str
    name: str
    datapath_type: str
    external_ids: dict[str, str]
    network_id: str | None = None

    @classmethod
    def from_domain(cls, b: TopologyBridge) -> TopologyBridgeOut:
        return cls(
            node_id=b.node_id,
            name=b.name,
            datapath_type=b.datapath_type,
            external_ids=dict(b.external_ids),
            network_id=b.network_id,
        )


class TopologyEdgeOut(BaseModel):
    kind: Literal["node_network", "vxlan_tunnel"]
    source: str
    target: str
    network_id: str | None = None

    @classmethod
    def from_domain(cls, e: TopologyEdge) -> TopologyEdgeOut:
        return cls(kind=e.kind, source=e.source, target=e.target, network_id=e.network_id)


class TopologyResponse(BaseModel):
    observed_at: datetime
    nodes: list[TopologyNodeOut]
    networks: list[TopologyNetworkOut]
    bridges: list[TopologyBridgeOut]
    edges: list[TopologyEdgeOut]

    @classmethod
    def from_domain(cls, t: Topology) -> TopologyResponse:
        return cls(
            observed_at=t.observed_at,
            nodes=[TopologyNodeOut.from_domain(n) for n in t.nodes],
            networks=[TopologyNetworkOut.from_domain(n) for n in t.networks],
            bridges=[TopologyBridgeOut.from_domain(b) for b in t.bridges],
            edges=[TopologyEdgeOut.from_domain(e) for e in t.edges],
        )


# ---------------------------------------------------------------------------
# Drift (M8)
# ---------------------------------------------------------------------------


class DriftItemOut(BaseModel):
    network_id: str
    node_id: str
    kind: Literal[
        "bridge_missing_or_changed",
        "bridge_orphan",
        "vxlan_port_missing_or_changed",
        "port_missing_or_changed",
        "port_orphan",
    ]
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, d: DriftItem) -> DriftItemOut:
        return cls(
            network_id=d.network_id,
            node_id=d.node_id,
            kind=d.kind,
            description=d.description,
            payload=dict(d.payload),
        )


class DriftReportResponse(BaseModel):
    scanned_at: datetime
    items: list[DriftItemOut]
    stale_nodes: list[str]

    @classmethod
    def from_domain(cls, r: DriftReport) -> DriftReportResponse:
        return cls(
            scanned_at=r.scanned_at,
            items=[DriftItemOut.from_domain(it) for it in r.items],
            stale_nodes=list(r.stale_nodes),
        )
