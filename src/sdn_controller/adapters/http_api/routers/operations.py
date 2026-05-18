"""Operation endpoints (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from sdn_controller.adapters.http_api.auth import require
from sdn_controller.adapters.http_api.dependencies import (
    GetOperationDep,
    ListOperationsDep,
)
from sdn_controller.adapters.http_api.schemas import (
    OperationEventsResponse,
    OperationListResponse,
    OperationOut,
)
from sdn_controller.core.value_objects.ids import OperationId
from sdn_controller.core.value_objects.security import Permission

router = APIRouter(
    prefix="/operations",
    tags=["operations"],
    dependencies=[Depends(require(Permission.OPERATION_READ))],
)


@router.get("", response_model=OperationListResponse, summary="List recent operations")
async def list_operations(
    use_case: ListOperationsDep,
    limit: int = Query(default=100, ge=1, le=1000),
) -> OperationListResponse:
    ops = await use_case.execute(limit=limit)
    return OperationListResponse(items=[OperationOut.from_domain(op) for op in ops])


@router.get("/{operation_id}", response_model=OperationOut, summary="Get an operation")
async def get_operation(operation_id: str, use_case: GetOperationDep) -> OperationOut:
    op = await use_case.execute(OperationId(operation_id))
    return OperationOut.from_domain(op)


@router.get(
    "/{operation_id}/events",
    response_model=OperationEventsResponse,
    summary="Get events for an operation",
)
async def get_operation_events(
    operation_id: str,
    use_case: GetOperationDep,
) -> OperationEventsResponse:
    op = await use_case.execute(OperationId(operation_id))
    return OperationEventsResponse(items=OperationOut.from_domain(op).events)
