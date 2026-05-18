"""In-process ``FakeAgent`` — drop-in ``AgentPort`` for tests and quick dev.

Implements just enough of an agent to make the reconciler look real:
* per-node mutable OVS state (bridges → ports → interfaces);
* per-step idempotency (``changed=False`` when the target already matches);
* deterministic ``state_hash`` (SHA-256 over the canonical OvsState shape);
* snapshot/restore via JSON dump round-trip.

Lives in the controller's adapter so tests can wire it through the same
container build as production — the production path uses ``HttpAgentClient``
against a real ``netos_agent`` process. The two are functionally
substitutable for everything use-case code does.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anyio

from sdn_controller.core.services.clock import Clock
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.errors import (
    NotFoundError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import NodeId
from sdn_controller.ports.agent import (
    DeleteBridgeStep,
    DeleteDhcpScopeStep,
    DeleteDnsZoneStep,
    DeleteFirewallPolicyStep,
    DeleteNatRuleStep,
    DeletePortStep,
    DhcpScopeStepSpec,
    DnsZoneStepSpec,
    EnsureBridgeStep,
    EnsureDhcpScopeStep,
    EnsureDnsZoneStep,
    EnsureFirewallPolicyStep,
    EnsureNatRuleStep,
    EnsurePortStep,
    EnsureVxlanPortStep,
    FirewallPolicyStepSpec,
    NatRuleStepSpec,
    OvsBridgeView,
    OvsInterfaceView,
    OvsPortView,
    OvsStateView,
    Plan,
    PlanResult,
    PlanStep,
    PlanStepResult,
    SnapshotRef,
)

# ---------------------------------------------------------------------------
# Internal mutable model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Port:
    name: str
    type: str = "internal"
    options: dict[str, str] = field(default_factory=dict)
    tag: int | None = None
    trunks: tuple[int, ...] = ()
    external_ids: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class _Bridge:
    name: str
    datapath_type: str = "system"
    ports: dict[str, _Port] = field(default_factory=dict)
    external_ids: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class _NodeState:
    bridges: dict[str, _Bridge] = field(default_factory=dict)
    dhcp_scopes: dict[str, DhcpScopeStepSpec] = field(default_factory=dict)
    dns_zones: dict[str, DnsZoneStepSpec] = field(default_factory=dict)
    nat_rules: dict[str, NatRuleStepSpec] = field(default_factory=dict)
    firewall_policies: dict[str, FirewallPolicyStepSpec] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# FakeAgent
# ---------------------------------------------------------------------------


class FakeAgent:
    """A controller-side ``AgentPort`` that simulates remote agents in memory."""

    def __init__(self, *, clock: Clock, ovs_version: str = "fake-3.2.0") -> None:
        self._clock = clock
        self._ovs_version = ovs_version
        self._by_node: dict[NodeId, _NodeState] = defaultdict(_NodeState)
        self._snapshots: dict[str, _Snapshot] = {}
        self._lock = anyio.Lock()
        self._next_snap = 0

    # -- AgentPort -----------------------------------------------------------

    async def get_capabilities(self, node_id: NodeId) -> NodeCapabilities:
        async with self._lock:
            state = self._by_node.get(node_id)
            interfaces = []
            features: set[str] = set()
            if state is not None:
                for bridge in state.bridges.values():
                    for port in bridge.ports.values():
                        if port.type in {"system", "internal"}:
                            interfaces.append(port.name)
                        if port.type == "vxlan":
                            features.add("vxlan")
        return NodeCapabilities(
            ovs_version=self._ovs_version,
            kernel=None,
            interfaces=tuple(interfaces),
            features=tuple(sorted(features)),
        )

    async def get_state(self, node_id: NodeId) -> OvsStateView:
        async with self._lock:
            state = self._by_node.get(node_id) or _NodeState()
            return _to_view(state, ovs_version=self._ovs_version)

    async def apply_plan(self, node_id: NodeId, plan: Plan) -> PlanResult:
        async with self._lock:
            state = self._by_node[node_id]  # default-created if absent
            results: list[PlanStepResult] = []
            overall_ok = True
            for step in plan.steps:
                try:
                    changed = _apply_step(state, step)
                except (NotFoundError, ValidationError) as exc:
                    overall_ok = False
                    results.append(
                        PlanStepResult(
                            action=step.action,
                            ok=False,
                            changed=False,
                            message=exc.message,
                            details={"code": exc.code},
                        )
                    )
                    continue
                results.append(
                    PlanStepResult(
                        action=step.action,
                        ok=True,
                        changed=changed,
                        message="applied" if changed else "noop",
                    )
                )
        return PlanResult(plan_id=plan.plan_id, ok=overall_ok, steps=tuple(results))

    async def snapshot(self, node_id: NodeId, *, label: str | None = None) -> SnapshotRef:
        async with self._lock:
            state = self._by_node.get(node_id) or _NodeState()
            self._next_snap += 1
            snap_id = f"snap_{self._next_snap}"
            payload = _dump(state)
            snap = _Snapshot(
                id=snap_id,
                node_id=node_id,
                created_at=self._clock.now(),
                payload=payload,
                state_hash=_hash(payload),
                label=label,
            )
            self._snapshots[snap_id] = snap
            return SnapshotRef(
                id=snap_id,
                state_hash=snap.state_hash,
                created_at=snap.created_at,
                label=snap.label,
            )

    async def restore(self, node_id: NodeId, snapshot_id: str) -> SnapshotRef:
        async with self._lock:
            snap = self._snapshots.get(snapshot_id)
            if snap is None or snap.node_id != node_id:
                raise NotFoundError(f"snapshot {snapshot_id} not found for node {node_id}")
            self._by_node[node_id] = _load(snap.payload)
            return SnapshotRef(
                id=snap.id,
                state_hash=snap.state_hash,
                created_at=snap.created_at,
                label=snap.label,
            )

    # -- inspection helpers (only used by tests) -----------------------------

    async def state_hash(self, node_id: NodeId) -> str:
        async with self._lock:
            return _hash(_dump(self._by_node.get(node_id) or _NodeState()))


@dataclass(slots=True)
class _Snapshot:
    id: str
    node_id: NodeId
    created_at: datetime
    payload: dict[str, Any]
    state_hash: str
    label: str | None = None


# ---------------------------------------------------------------------------
# Step dispatch (mutates ``state`` in place; returns ``changed``)
# ---------------------------------------------------------------------------


def _apply_step(state: _NodeState, step: PlanStep) -> bool:  # noqa: PLR0911, PLR0912 — wide dispatch
    match step:
        case EnsureBridgeStep():
            return _ensure_bridge(
                state,
                name=step.name,
                datapath_type=step.datapath_type,
                external_ids=dict(step.external_ids),
            )
        case DeleteBridgeStep():
            return state.bridges.pop(step.name, None) is not None
        case EnsurePortStep():
            return _ensure_port(
                state,
                bridge=step.bridge,
                name=step.name,
                type=step.type,
                options=dict(step.options),
                tag=step.tag,
                trunks=tuple(step.trunks),
                external_ids=dict(step.external_ids),
            )
        case DeletePortStep():
            br = _require_bridge(state, step.bridge)
            return br.ports.pop(step.name, None) is not None
        case EnsureVxlanPortStep():
            options: dict[str, str] = {
                "key": str(step.vni),
                "remote_ip": step.remote_ip,
                "dst_port": str(step.dst_port),
            }
            if step.local_ip is not None:
                options["local_ip"] = step.local_ip
            if step.mtu is not None:
                options["mtu_request"] = str(step.mtu)
            return _ensure_port(
                state,
                bridge=step.bridge,
                name=step.name,
                type="vxlan",
                options=options,
                tag=None,
                trunks=(),
                external_ids=dict(step.external_ids),
            )
        # -- M7 edge services ------------------------------------------------
        case EnsureDhcpScopeStep():
            existing = state.dhcp_scopes.get(step.spec.scope_id)
            if existing == step.spec:
                return False
            state.dhcp_scopes[step.spec.scope_id] = step.spec
            return True
        case DeleteDhcpScopeStep():
            return state.dhcp_scopes.pop(step.scope_id, None) is not None
        case EnsureDnsZoneStep():
            existing_zone = state.dns_zones.get(step.spec.zone)
            if existing_zone == step.spec:
                return False
            state.dns_zones[step.spec.zone] = step.spec
            return True
        case DeleteDnsZoneStep():
            return state.dns_zones.pop(step.zone, None) is not None
        case EnsureNatRuleStep():
            existing_nat = state.nat_rules.get(step.spec.rule_id)
            if existing_nat == step.spec:
                return False
            state.nat_rules[step.spec.rule_id] = step.spec
            return True
        case DeleteNatRuleStep():
            return state.nat_rules.pop(step.rule_id, None) is not None
        case EnsureFirewallPolicyStep():
            existing_fw = state.firewall_policies.get(step.spec.policy_id)
            if existing_fw == step.spec:
                return False
            state.firewall_policies[step.spec.policy_id] = step.spec
            return True
        case DeleteFirewallPolicyStep():
            return state.firewall_policies.pop(step.policy_id, None) is not None


def _ensure_bridge(
    state: _NodeState,
    *,
    name: str,
    datapath_type: str,
    external_ids: dict[str, str],
) -> bool:
    existing = state.bridges.get(name)
    if existing is None:
        state.bridges[name] = _Bridge(
            name=name, datapath_type=datapath_type, external_ids=dict(sorted(external_ids.items()))
        )
        return True
    changed = False
    if existing.datapath_type != datapath_type:
        existing.datapath_type = datapath_type
        changed = True
    ids_sorted = dict(sorted(external_ids.items()))
    if existing.external_ids != ids_sorted:
        existing.external_ids = ids_sorted
        changed = True
    return changed


def _ensure_port(
    state: _NodeState,
    *,
    bridge: str,
    name: str,
    type: str,
    options: dict[str, str],
    tag: int | None,
    trunks: tuple[int, ...],
    external_ids: dict[str, str],
) -> bool:
    br = _require_bridge(state, bridge)
    new = _Port(
        name=name,
        type=type,
        options=dict(sorted(options.items())),
        tag=tag,
        trunks=tuple(sorted(trunks)),
        external_ids=dict(sorted(external_ids.items())),
    )
    existing = br.ports.get(name)
    if existing is None:
        br.ports[name] = new
        return True
    if (
        existing.type == new.type
        and existing.options == new.options
        and existing.tag == new.tag
        and existing.trunks == new.trunks
        and existing.external_ids == new.external_ids
    ):
        return False
    br.ports[name] = new
    return True


def _require_bridge(state: _NodeState, name: str) -> _Bridge:
    try:
        return state.bridges[name]
    except KeyError as exc:
        raise NotFoundError(f"bridge {name!r} does not exist") from exc


# ---------------------------------------------------------------------------
# Snapshot serialization
# ---------------------------------------------------------------------------


def _dump(state: _NodeState) -> dict[str, Any]:
    return {"bridges": [_bridge_to_dict(b) for b in state.bridges.values()]}


def _load(payload: dict[str, Any]) -> _NodeState:
    new = _NodeState()
    for b in payload.get("bridges") or []:
        bridge = _bridge_from_dict(b)
        new.bridges[bridge.name] = bridge
    return new


def _bridge_to_dict(b: _Bridge) -> dict[str, Any]:
    return {
        "name": b.name,
        "datapath_type": b.datapath_type,
        "external_ids": dict(b.external_ids),
        "ports": [_port_to_dict(p) for p in b.ports.values()],
    }


def _bridge_from_dict(d: dict[str, Any]) -> _Bridge:
    bridge = _Bridge(
        name=str(d["name"]),
        datapath_type=str(d.get("datapath_type") or "system"),
        external_ids=dict(d.get("external_ids") or {}),
    )
    for p in d.get("ports") or []:
        port = _port_from_dict(p)
        bridge.ports[port.name] = port
    return bridge


def _port_to_dict(p: _Port) -> dict[str, Any]:
    return {
        "name": p.name,
        "type": p.type,
        "options": dict(p.options),
        "tag": p.tag,
        "trunks": list(p.trunks),
        "external_ids": dict(p.external_ids),
    }


def _port_from_dict(d: dict[str, Any]) -> _Port:
    return _Port(
        name=str(d["name"]),
        type=str(d.get("type") or "internal"),
        options=dict(d.get("options") or {}),
        tag=d.get("tag"),
        trunks=tuple(int(t) for t in (d.get("trunks") or ())),
        external_ids=dict(d.get("external_ids") or {}),
    )


# ---------------------------------------------------------------------------
# View + hash
# ---------------------------------------------------------------------------


def _to_view(state: _NodeState, *, ovs_version: str) -> OvsStateView:
    payload = _dump(state)
    return OvsStateView(
        ovs_version=ovs_version,
        bridges=tuple(_bridge_to_view(b) for b in state.bridges.values()),
        state_hash=_hash(payload),
    )


def _bridge_to_view(b: _Bridge) -> OvsBridgeView:
    return OvsBridgeView(
        name=b.name,
        datapath_type=b.datapath_type,
        external_ids=dict(b.external_ids),
        ports=tuple(_port_to_view(p) for p in b.ports.values()),
    )


def _port_to_view(p: _Port) -> OvsPortView:
    return OvsPortView(
        name=p.name,
        tag=p.tag,
        trunks=tuple(p.trunks),
        external_ids=dict(p.external_ids),
        interfaces=(OvsInterfaceView(name=p.name, type=p.type, options=dict(p.options)),),
    )


def _hash(payload: dict[str, Any]) -> str:
    # Hash a sorted copy so dict insertion order doesn't change the hash.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# silence unused-import warnings for symbols we re-use across module
_ = copy
