"""``/audit-events`` — read-only лента (M10 — SDN-033).

Доступ — только admin. Лента самая чувствительная: она показывает, кто
что делал, и любая лишняя возможность это видеть подсказывает
злоумышленнику стратегию.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from sdn_controller.adapters.http_api.auth import require
from sdn_controller.adapters.http_api.dependencies import ListAuditEventsDep
from sdn_controller.adapters.http_api.schemas import (
    AuditEventListResponse,
    AuditEventOut,
)
from sdn_controller.core.use_cases.audit import ListAuditEventsCommand
from sdn_controller.core.value_objects.security import Permission

router = APIRouter(
    prefix="/audit-events",
    tags=["audit"],
    dependencies=[Depends(require(Permission.AUDIT_READ))],
)


@router.get(
    "",
    response_model=AuditEventListResponse,
    summary="Лента аудита (admin)",
)
async def list_audit_events(
    use_case: ListAuditEventsDep,
    actor: Annotated[str | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    resource_type: Annotated[str | None, Query()] = None,
    resource_id: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> AuditEventListResponse:
    items = await use_case.execute(
        ListAuditEventsCommand(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            since=since,
            limit=limit,
        )
    )
    return AuditEventListResponse(items=[AuditEventOut.from_domain(it) for it in items])
