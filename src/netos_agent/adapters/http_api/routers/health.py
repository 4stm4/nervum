"""Liveness + readiness probes.

* ``/healthz`` is unconditional — the process is up.
* ``/readyz`` actually touches OVSDB. We don't want the controller to drive
  plans against an agent whose backend isn't reachable.
"""

from __future__ import annotations

from fastapi import APIRouter

from netos_agent.adapters.http_api.dependencies import GetOvsStateDep
from netos_agent.adapters.http_api.schemas import HealthResponse, ReadyResponse
from netos_agent.core.value_objects.errors import OvsdbError

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
async def healthz() -> HealthResponse:
    return HealthResponse()


@router.get("/readyz", response_model=ReadyResponse, summary="Readiness probe (OVSDB reachable)")
async def readyz(get_ovs_state: GetOvsStateDep) -> ReadyResponse:
    try:
        state = await get_ovs_state.execute()
    except OvsdbError as exc:
        return ReadyResponse(status="not_ready", reason=exc.message)
    return ReadyResponse(status="ok", ovs_version=state.ovs_version)
