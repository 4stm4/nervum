"""``/nodes/{id}/snapshots`` + restore (M11 — SDN-035).

Эти ручки дёргают агент через ``AgentPort``. Чтение каталога —
``snapshot:read``, создание/restore — ``snapshot:write`` (последнее
особенно разрушительно: restore переписывает всё OVS-состояние узла,
поэтому viewer'у и оператору сети туда нельзя).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from sdn_controller.adapters.http_api.auth import require
from sdn_controller.adapters.http_api.dependencies import (
    ListNodeSnapshotsDep,
    RestoreNodeSnapshotDep,
    TakeNodeSnapshotDep,
)
from sdn_controller.adapters.http_api.schemas import (
    NodeSnapshotListResponse,
    NodeSnapshotOut,
    NodeSnapshotRestoreResponse,
    TakeSnapshotRequest,
)
from sdn_controller.core.use_cases.node_snapshots import TakeSnapshotCommand
from sdn_controller.core.value_objects.ids import NodeId, NodeSnapshotId
from sdn_controller.core.value_objects.security import Permission

router = APIRouter(tags=["snapshots"])


@router.get(
    "/nodes/{node_id}/snapshots",
    response_model=NodeSnapshotListResponse,
    summary="Список снапшотов узла",
    dependencies=[Depends(require(Permission.SNAPSHOT_READ))],
)
async def list_node_snapshots(
    node_id: str,
    use_case: ListNodeSnapshotsDep,
) -> NodeSnapshotListResponse:
    items = await use_case.execute(NodeId(node_id))
    return NodeSnapshotListResponse(items=[NodeSnapshotOut.from_domain(s) for s in items])


@router.post(
    "/nodes/{node_id}/snapshots",
    response_model=NodeSnapshotOut,
    status_code=status.HTTP_201_CREATED,
    summary="Снять снапшот узла (дёргает agent.snapshot)",
    dependencies=[Depends(require(Permission.SNAPSHOT_WRITE))],
)
async def take_node_snapshot(
    node_id: str,
    payload: TakeSnapshotRequest,
    use_case: TakeNodeSnapshotDep,
) -> NodeSnapshotOut:
    snap = await use_case.execute(
        TakeSnapshotCommand(node_id=NodeId(node_id), label=payload.label),
    )
    return NodeSnapshotOut.from_domain(snap)


@router.post(
    "/node-snapshots/{snapshot_id}/restore",
    response_model=NodeSnapshotRestoreResponse,
    summary="Восстановить узел из снапшота (дёргает agent.restore)",
    dependencies=[Depends(require(Permission.SNAPSHOT_WRITE))],
)
async def restore_node_snapshot(
    snapshot_id: str,
    use_case: RestoreNodeSnapshotDep,
) -> NodeSnapshotRestoreResponse:
    result = await use_case.execute(NodeSnapshotId(snapshot_id))
    return NodeSnapshotRestoreResponse(snapshot=NodeSnapshotOut.from_domain(result.snapshot))
