"""``/v1/node/*`` — info, capabilities, composite state."""

from __future__ import annotations

from fastapi import APIRouter

from netos_agent.adapters.http_api.dependencies import (
    GetNodeStateDep,
    GetSystemInfoDep,
)
from netos_agent.adapters.http_api.schemas import (
    NodeStateOut,
    SystemInfoOut,
)

router = APIRouter(prefix="/v1/node", tags=["node"])


@router.get("/info", response_model=SystemInfoOut, summary="Slow-changing node identity")
async def info(get_info: GetSystemInfoDep) -> SystemInfoOut:
    return SystemInfoOut.from_domain(await get_info.execute())


@router.get("/state", response_model=NodeStateOut, summary="Composite of info + OVS state")
async def state(get_state: GetNodeStateDep) -> NodeStateOut:
    return NodeStateOut.from_domain(await get_state.execute())


@router.get(
    "/capabilities",
    response_model=NodeStateOut,
    summary=("Capabilities snapshot the controller stores on enrolment (alias of /state for now)"),
)
async def capabilities(get_state: GetNodeStateDep) -> NodeStateOut:
    # Capabilities are derived from info + OVS state; we share the route body
    # for now and split the schema later if we need to drop volatile fields.
    return NodeStateOut.from_domain(await get_state.execute())
