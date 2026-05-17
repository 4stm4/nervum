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
    "DeletePortStep",
    "EnsureBridgeStep",
    "EnsurePortStep",
    "EnsureVxlanPortStep",
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


PlanStep = (
    EnsureBridgeStep | DeleteBridgeStep | EnsurePortStep | DeletePortStep | EnsureVxlanPortStep
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
