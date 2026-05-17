"""``/v1/network/*`` — plan apply (and rollback alias)."""

from __future__ import annotations

from fastapi import APIRouter

from netos_agent.adapters.http_api.dependencies import (
    ApplyPlanDep,
    GetOvsStateDep,
    RestoreDep,
)
from netos_agent.adapters.http_api.schemas import (
    OvsStateOut,
    PlanApplyRequest,
    PlanResultOut,
    SnapshotOut,
    SnapshotRestoreResponse,
)
from netos_agent.core.value_objects.ids import SnapshotId

router = APIRouter(prefix="/v1/network", tags=["network"])


@router.post("/apply", response_model=PlanResultOut, summary="Apply a structured plan")
async def apply(payload: PlanApplyRequest, apply_plan: ApplyPlanDep) -> PlanResultOut:
    result = await apply_plan.execute(payload.to_domain())
    return PlanResultOut.from_domain(result)


@router.post(
    "/rollback/{snapshot_id}",
    response_model=SnapshotRestoreResponse,
    summary="Roll back to a previously captured snapshot (alias of /v1/ovs/restore/{id})",
)
async def rollback(
    snapshot_id: str,
    restore: RestoreDep,
    get_state: GetOvsStateDep,
) -> SnapshotRestoreResponse:
    snap = await restore.execute(SnapshotId(snapshot_id))
    state = await get_state.execute()
    return SnapshotRestoreResponse(
        snapshot=SnapshotOut.from_domain(snap),
        ovs_state=OvsStateOut.from_domain(state),
    )
