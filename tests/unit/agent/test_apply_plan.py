"""ApplyPlan use case — drives ``OvsdbPort`` and surfaces ``changed``/``ok`` flags."""

from __future__ import annotations

from netos_agent.adapters.ovsdb_fake import FakeOvsdb
from netos_agent.core.use_cases.apply_plan import ApplyPlan
from netos_agent.core.value_objects.plan import (
    DeletePortStep,
    EnsureBridgeStep,
    EnsurePortStep,
    EnsureVxlanPortStep,
    Plan,
)


async def test_apply_plan_creates_bridges_and_ports() -> None:
    db = FakeOvsdb()
    apply = ApplyPlan(ovsdb=db)
    plan = Plan(
        plan_id="plan_1",
        steps=(
            EnsureBridgeStep(name="br-int"),
            EnsurePortStep(bridge="br-int", name="patch-tun", type="patch"),
        ),
    )

    result = await apply.execute(plan)

    assert result.ok is True
    assert result.plan_id == "plan_1"
    assert [(s.action, s.ok, s.changed) for s in result.steps] == [
        ("ensure_bridge", True, True),
        ("ensure_port", True, True),
    ]


async def test_apply_plan_is_idempotent() -> None:
    db = FakeOvsdb()
    apply = ApplyPlan(ovsdb=db)
    plan = Plan(
        plan_id="plan_1",
        steps=(
            EnsureBridgeStep(name="br-int"),
            EnsurePortStep(bridge="br-int", name="p1"),
        ),
    )

    await apply.execute(plan)
    again = await apply.execute(plan)

    assert again.ok is True
    assert all(s.changed is False for s in again.steps)


async def test_step_failure_surfaces_structured_error() -> None:
    db = FakeOvsdb()
    apply = ApplyPlan(ovsdb=db)
    plan = Plan(
        plan_id="plan_err",
        steps=(EnsurePortStep(bridge="missing-bridge", name="p1"),),
    )

    result = await apply.execute(plan)

    assert result.ok is False
    assert result.steps[0].ok is False
    assert result.steps[0].details["code"] == "not_found"
    assert "missing-bridge" in result.steps[0].message


async def test_plan_continues_after_failed_step() -> None:
    db = FakeOvsdb()
    apply = ApplyPlan(ovsdb=db)
    plan = Plan(
        plan_id="plan_mixed",
        steps=(
            EnsurePortStep(bridge="missing", name="p1"),  # fails
            EnsureBridgeStep(name="br-tun"),  # succeeds
            EnsureVxlanPortStep(bridge="br-tun", name="vxlan-1", vni=10, remote_ip="10.0.0.2"),
        ),
    )

    result = await apply.execute(plan)

    assert result.ok is False
    assert [s.ok for s in result.steps] == [False, True, True]


async def test_delete_port_reports_changed_when_present() -> None:
    db = FakeOvsdb()
    await db.ensure_bridge(name="br-a")
    await db.ensure_port(bridge="br-a", name="p1")
    apply = ApplyPlan(ovsdb=db)

    result = await apply.execute(
        Plan(plan_id="x", steps=(DeletePortStep(bridge="br-a", name="p1"),))
    )

    assert result.steps[0].ok is True
    assert result.steps[0].changed is True
