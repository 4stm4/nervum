"""Plan, plan-step variants, and per-step result types.

The controller never sends shell commands — it sends a sequence of
high-level steps the agent decides how to apply. Each step is structured,
typed and validated at the boundary, which means:

* the agent is the only place that knows how to talk to OVS;
* the same plan JSON works against any backend (real OVSDB or fake);
* a malicious or buggy controller cannot escape into arbitrary commands.

Today (M3) we support bridge and port primitives. VLAN/VXLAN-specific
variants live here too so the wire model is stable; the FakeOvsdb adapter
implements the bridge/port ones end-to-end and stubs the rest with a clear
``not yet implemented`` so we don't ship a half-wired contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Step variants
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnsureBridgeStep:
    name: str
    datapath_type: str = "system"
    # ``external_ids`` tag the bridge with controller-owned metadata
    # (e.g. ``{"owner": "sdn-controller", "network_id": "net_..."}``) so
    # cleanup can find what it owns without ambient state.
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
    # Optional tunnel-source IP. OVS will pick the kernel route otherwise; we
    # set it when the controller wants to pin VXLAN to a specific iface.
    local_ip: str | None = None
    dst_port: int = 4789
    mtu: int | None = None
    external_ids: dict[str, str] = field(default_factory=dict)
    action: Literal["ensure_vxlan_port"] = "ensure_vxlan_port"


PlanStep = (
    EnsureBridgeStep | DeleteBridgeStep | EnsurePortStep | DeletePortStep | EnsureVxlanPortStep
)


# ---------------------------------------------------------------------------
# Plan + result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    steps: tuple[PlanStep, ...]


@dataclass(frozen=True, slots=True)
class PlanStepResult:
    """Outcome of a single step.

    ``changed`` is the *idempotency signal* — ``ok=True, changed=False`` means
    the step was a no-op (state already matched). The controller's reconciler
    uses this to tell genuine drift from successful re-applies.
    """

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
