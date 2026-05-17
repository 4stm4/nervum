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
    Network,
    Node,
    Operation,
    OperationEvent,
    Subnet,
)
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
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

    @classmethod
    def from_domain(cls, sub: Subnet) -> SubnetOut:
        return cls(id=sub.id, cidr=sub.cidr, gateway=sub.gateway)


class NetworkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    type: NetworkType
    mtu: int = Field(default=1500, ge=576, le=9216)
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    vni: int | None = Field(default=None, ge=1, le=16_777_215)
    subnet: SubnetIn | None = None
    labels: dict[str, str] = Field(default_factory=dict)


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
            created_at=net.created_at,
            updated_at=net.updated_at,
        )


class NetworkListResponse(BaseModel):
    items: list[NetworkOut]


class NetworkCreateResponse(BaseModel):
    """Combined envelope: the resource ``and`` the operation that produced it."""

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
