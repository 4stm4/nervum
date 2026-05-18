"""Translate a structured ``Plan`` into port calls.

Each step gets its own try/except so one failing step doesn't poison the
whole result — we return a structured ``PlanResult`` listing exactly what
happened, with per-step ``ok``/``changed`` flags. The controller's
reconciler trusts these flags to detect drift and to decide whether to
trigger a rollback.

Idempotency comes from the underlying adapter: ``ensure_*`` returns
``changed=False`` when the target already matched. ApplyPlan surfaces
the answer.

M7 adds DHCP, DNS, NAT and firewall step variants. For each ``ensure_*``
edge-service step we validate first (so a malformed config never reaches
``apply``) and then commit; that matches the plan's "config проверяется
перед применением" acceptance.
"""

from __future__ import annotations

from netos_agent.core.value_objects.errors import AgentError
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
from netos_agent.ports.dhcp import DhcpPort
from netos_agent.ports.dns import DnsPort
from netos_agent.ports.firewall import FirewallPort
from netos_agent.ports.ovsdb import OvsdbPort


class ApplyPlan:
    def __init__(
        self,
        *,
        ovsdb: OvsdbPort,
        dhcp: DhcpPort,
        dns: DnsPort,
        firewall: FirewallPort,
    ) -> None:
        self._ovsdb = ovsdb
        self._dhcp = dhcp
        self._dns = dns
        self._firewall = firewall

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

    async def _apply_step(self, step: PlanStep) -> bool:  # noqa: PLR0911, PLR0912 — discriminated dispatch
        match step:
            # -- OVS ----------------------------------------------------------
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
            # -- DHCP ---------------------------------------------------------
            case EnsureDhcpScopeStep():
                await self._dhcp.validate(step.spec)
                return await self._dhcp.apply(step.spec)
            case DeleteDhcpScopeStep():
                return await self._dhcp.delete(step.scope_id)
            # -- DNS ----------------------------------------------------------
            case EnsureDnsZoneStep():
                await self._dns.validate(step.spec)
                return await self._dns.apply(step.spec)
            case DeleteDnsZoneStep():
                return await self._dns.delete(step.zone)
            # -- NAT ----------------------------------------------------------
            case EnsureNatRuleStep():
                await self._firewall.validate_nat(step.spec)
                return await self._firewall.apply_nat(step.spec)
            case DeleteNatRuleStep():
                return await self._firewall.delete_nat(step.rule_id)
            # -- Firewall -----------------------------------------------------
            case EnsureFirewallPolicyStep():
                await self._firewall.validate_policy(step.spec)
                return await self._firewall.apply_policy(step.spec)
            case DeleteFirewallPolicyStep():
                return await self._firewall.delete_policy(step.policy_id)
