"""Translate a structured ``Plan`` into ``OvsdbPort`` calls.

Each step gets its own try/except so one failing step doesn't poison the
whole result — we return a structured ``PlanResult`` listing exactly what
happened, with per-step ``ok``/``changed`` flags. The controller's
reconciler trusts these flags to detect drift and to decide whether to
trigger a rollback.

Idempotency comes from the underlying adapter: ``OvsdbPort.ensure_*``
returns ``changed=False`` when the target already matched. ApplyPlan just
surfaces the answer.
"""

from __future__ import annotations

from netos_agent.core.value_objects.errors import AgentError
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
from netos_agent.ports.ovsdb import OvsdbPort


class ApplyPlan:
    def __init__(self, *, ovsdb: OvsdbPort) -> None:
        self._ovsdb = ovsdb

    async def execute(self, plan: Plan) -> PlanResult:
        results: list[PlanStepResult] = []
        overall_ok = True
        for step in plan.steps:
            try:
                changed = await self._apply_step(step)
            except AgentError as exc:
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
                # Subsequent steps may still run — the controller decides
                # whether to roll back based on the aggregate result.
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

    async def _apply_step(self, step: PlanStep) -> bool:
        match step:
            case EnsureBridgeStep():
                return await self._ovsdb.ensure_bridge(
                    name=step.name,
                    datapath_type=step.datapath_type,
                    external_ids=dict(step.external_ids),
                )
            case DeleteBridgeStep():
                return await self._ovsdb.delete_bridge(name=step.name)
            case EnsurePortStep():
                return await self._ovsdb.ensure_port(
                    bridge=step.bridge,
                    name=step.name,
                    type=step.type,
                    options=dict(step.options),
                    tag=step.tag,
                    trunks=tuple(step.trunks),
                    external_ids=dict(step.external_ids),
                )
            case DeletePortStep():
                return await self._ovsdb.delete_port(bridge=step.bridge, name=step.name)
            case EnsureVxlanPortStep():
                return await self._ovsdb.ensure_vxlan_port(
                    bridge=step.bridge,
                    name=step.name,
                    vni=step.vni,
                    remote_ip=step.remote_ip,
                    local_ip=step.local_ip,
                    dst_port=step.dst_port,
                    mtu=step.mtu,
                    external_ids=dict(step.external_ids),
                )
