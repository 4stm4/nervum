"""``/v1/ovs/*`` — live state, snapshot, restore."""

from __future__ import annotations

from fastapi import APIRouter, status

from netos_agent.adapters.http_api.dependencies import (
    GetOvsStateDep,
    ListSnapshotsDep,
    RestoreDep,
    SnapshotDep,
)
from netos_agent.adapters.http_api.schemas import (
    OvsStateOut,
    SnapshotCreateRequest,
    SnapshotListResponse,
    SnapshotOut,
    SnapshotRestoreResponse,
)
from netos_agent.core.value_objects.ids import SnapshotId

router = APIRouter(prefix="/v1/ovs", tags=["ovs"])


@router.get("/state", response_model=OvsStateOut, summary="Read-only OVS state + stable hash")
async def state(get_state: GetOvsStateDep) -> OvsStateOut:
    return OvsStateOut.from_domain(await get_state.execute())


@router.get("/snapshots", response_model=SnapshotListResponse, summary="List persisted snapshots")
async def list_snapshots(list_use_case: ListSnapshotsDep) -> SnapshotListResponse:
    items = await list_use_case.execute()
    return SnapshotListResponse(items=[SnapshotOut.from_domain(s) for s in items])


@router.post(
    "/snapshot",
    response_model=SnapshotOut,
    status_code=status.HTTP_201_CREATED,
    summary="Snapshot the current OVS state",
)
async def take_snapshot(
    payload: SnapshotCreateRequest,
    snapshot: SnapshotDep,
) -> SnapshotOut:
    snap = await snapshot.execute(label=payload.label)
    return SnapshotOut.from_domain(snap)


@router.post(
    "/restore/{snapshot_id}",
    response_model=SnapshotRestoreResponse,
    summary="Restore OVS state from a snapshot",
)
async def restore_snapshot(
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
