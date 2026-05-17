"""Pydantic models for the agent's wire responses.

Parsing through Pydantic gives us:

* loud failures when the agent changes its contract (a schema drift sentinel),
* explicit, reviewable shapes for what we trust over the network,
* clean conversion into controller-side ``OvsStateView`` / ``SnapshotRef``.

These are private to the adapter. Use cases never see them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.ports.agent import (
    OvsBridgeView,
    OvsInterfaceView,
    OvsPortView,
    OvsStateView,
    PlanResult,
    PlanStepResult,
    SnapshotRef,
)

# ---------------------------------------------------------------------------
# Error envelope (matches the agent's HTTP layer 1:1)
# ---------------------------------------------------------------------------


class ErrorBodyIn(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelopeIn(BaseModel):
    error: ErrorBodyIn


# ---------------------------------------------------------------------------
# OVS state
# ---------------------------------------------------------------------------


class InterfaceIn(BaseModel):
    name: str
    type: str
    options: dict[str, str] = Field(default_factory=dict)
    admin_state: str = "up"

    def to_view(self) -> OvsInterfaceView:
        return OvsInterfaceView(
            name=self.name,
            type=self.type,
            options=dict(self.options),
            admin_state=self.admin_state,
        )


class PortIn(BaseModel):
    name: str
    tag: int | None = None
    trunks: list[int] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    interfaces: list[InterfaceIn] = Field(default_factory=list)

    def to_view(self) -> OvsPortView:
        return OvsPortView(
            name=self.name,
            tag=self.tag,
            trunks=tuple(self.trunks),
            external_ids=dict(self.external_ids),
            interfaces=tuple(i.to_view() for i in self.interfaces),
        )


class BridgeIn(BaseModel):
    name: str
    datapath_type: str = "system"
    external_ids: dict[str, str] = Field(default_factory=dict)
    ports: list[PortIn] = Field(default_factory=list)

    def to_view(self) -> OvsBridgeView:
        return OvsBridgeView(
            name=self.name,
            datapath_type=self.datapath_type,
            external_ids=dict(self.external_ids),
            ports=tuple(p.to_view() for p in self.ports),
        )


class OvsStateIn(BaseModel):
    ovs_version: str | None = None
    bridges: list[BridgeIn] = Field(default_factory=list)
    state_hash: str = ""

    def to_view(self) -> OvsStateView:
        return OvsStateView(
            ovs_version=self.ovs_version,
            bridges=tuple(b.to_view() for b in self.bridges),
            state_hash=self.state_hash,
        )


# ---------------------------------------------------------------------------
# Plan result
# ---------------------------------------------------------------------------


class PlanStepResultIn(BaseModel):
    action: str
    ok: bool
    changed: bool = False
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)

    def to_domain(self) -> PlanStepResult:
        return PlanStepResult(
            action=self.action,
            ok=self.ok,
            changed=self.changed,
            message=self.message,
            details=dict(self.details),
        )


class PlanResultIn(BaseModel):
    plan_id: str
    ok: bool
    steps: list[PlanStepResultIn] = Field(default_factory=list)

    def to_domain(self) -> PlanResult:
        return PlanResult(
            plan_id=self.plan_id,
            ok=self.ok,
            steps=tuple(s.to_domain() for s in self.steps),
        )


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class SnapshotIn(BaseModel):
    id: str
    state_hash: str
    created_at: datetime
    label: str | None = None

    def to_ref(self) -> SnapshotRef:
        return SnapshotRef(
            id=self.id,
            state_hash=self.state_hash,
            created_at=self.created_at,
            label=self.label,
        )


class SnapshotRestoreIn(BaseModel):
    """Response shape for ``POST /v1/ovs/restore/{id}``."""

    snapshot: SnapshotIn
    ovs_state: OvsStateIn


# ---------------------------------------------------------------------------
# Node info / capabilities
# ---------------------------------------------------------------------------


class SystemInfoIn(BaseModel):
    hostname: str
    kernel: str | None = None
    cpu_count: int | None = None
    memory_total_bytes: int | None = None


class NodeStateIn(BaseModel):
    info: SystemInfoIn
    ovs_state: OvsStateIn

    def to_capabilities(self) -> NodeCapabilities:
        """Distil the heavier ``NodeStateIn`` into the controller's
        ``NodeCapabilities`` shape.

        The capabilities the controller cares about are: OVS version, kernel,
        physical interface names (i.e. non-internal, non-vxlan port names),
        and a set of feature flags derived from what's present in OVS today.
        Anything fancier (CPU/memory) lives in node_state if a use case ever
        needs it.
        """
        ovs = self.ovs_state
        interfaces: list[str] = []
        features: set[str] = set()
        for bridge in ovs.bridges:
            for port in bridge.ports:
                for iface in port.interfaces:
                    if iface.type in {"system", "internal"} and iface.name not in interfaces:
                        interfaces.append(iface.name)
                    if iface.type == "vxlan":
                        features.add("vxlan")
        return NodeCapabilities(
            ovs_version=ovs.ovs_version,
            kernel=self.info.kernel,
            interfaces=tuple(interfaces),
            features=tuple(sorted(features)),
        )
