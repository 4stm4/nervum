"""``/events`` — snapshot + tail для подписчиков (SDN-057).

Парные ручки:

* ``GET /events/snapshot`` — текущее состояние сетей и узлов +
  monotonic ``event_id``. Подписчик с него стартует.
* ``GET /events?since=<event_id>`` — outbox-страница с
  ``event_id > since``; ``head_event_id`` в ответе сообщает «верхнюю
  границу», чтобы клиент знал, есть ли ещё хвост, и не делал лишний
  long-poll.

Read-only; на уровне RBAC обе ручки требуют ``webhook:read`` (тех же
прав, что и список подписок — это один и тот же интеграционный
контур).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from sdn_controller.adapters.http_api.auth import require
from sdn_controller.adapters.http_api.dependencies import (
    ExportSnapshotDep,
    ListEventsDep,
)
from sdn_controller.adapters.http_api.schemas import (
    EventsPageResponse,
    NetworkOut,
    NodeOut,
    OutboxEventOut,
    SnapshotResponse,
)
from sdn_controller.core.value_objects.security import Permission

router = APIRouter(
    prefix="/events",
    tags=["events"],
    dependencies=[Depends(require(Permission.WEBHOOK_READ))],
)


@router.get(
    "/snapshot",
    response_model=SnapshotResponse,
    summary="Snapshot of controller state + event_id watermark",
)
async def get_snapshot(use_case: ExportSnapshotDep) -> SnapshotResponse:
    snap = await use_case.execute()
    return SnapshotResponse(
        event_id=snap.event_id,
        networks=[NetworkOut.from_domain(n) for n in snap.networks],
        nodes=[NodeOut.from_domain(n) for n in snap.nodes],
    )


@router.get(
    "",
    response_model=EventsPageResponse,
    summary="Outbox tail: events with event_id > since",
)
async def list_events(
    use_case: ListEventsDep,
    since: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> EventsPageResponse:
    page = await use_case.execute(since=since, limit=limit)
    return EventsPageResponse(
        head_event_id=page.head_event_id,
        items=[
            OutboxEventOut(
                event_id=e.event_id,
                id=e.id,
                event_type=e.event_type,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                schema_version=e.schema_version,
                project_id=e.project_id,
                occurred_at=e.occurred_at,
                payload=dict(e.payload),
            )
            for e in page.items
        ],
    )
