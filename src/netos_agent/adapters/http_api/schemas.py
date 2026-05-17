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
from netos_agent.core.value_objects.plan import (
    DeleteBridgeStep,
    DeletePortStep,
    EnsureBridgeStep,
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
    interfaces: list[InterfaceOut]

    @classmethod
    def from_domain(cls, port: PortState) -> PortOut:
        return cls(
            name=port.name,
            tag=port.tag,
            trunks=list(port.trunks),
            interfaces=[InterfaceOut.from_domain(i) for i in port.interfaces],
        )


class BridgeOut(BaseModel):
    name: str
    datapath_type: str
    ports: list[PortOut]

    @classmethod
    def from_domain(cls, bridge: BridgeState) -> BridgeOut:
        return cls(
            name=bridge.name,
            datapath_type=bridge.datapath_type,
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
    dst_port: int = Field(default=4789, ge=1, le=65535)


PlanStepIn = Annotated[
    EnsureBridgeStepIn
    | DeleteBridgeStepIn
    | EnsurePortStepIn
    | DeletePortStepIn
    | EnsureVxlanPortStepIn,
    Field(discriminator="action"),
]


class PlanApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=128)
    steps: list[PlanStepIn] = Field(min_length=1)

    def to_domain(self) -> Plan:
        return Plan(plan_id=self.plan_id, steps=tuple(_step_to_domain(s) for s in self.steps))


def _step_to_domain(step: PlanStepIn) -> PlanStep:
    match step:
        case EnsureBridgeStepIn():
            return EnsureBridgeStep(name=step.name, datapath_type=step.datapath_type)
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
            )
        case DeletePortStepIn():
            return DeletePortStep(bridge=step.bridge, name=step.name)
        case EnsureVxlanPortStepIn():
            return EnsureVxlanPortStep(
                bridge=step.bridge,
                name=step.name,
                vni=step.vni,
                remote_ip=step.remote_ip,
                dst_port=step.dst_port,
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
