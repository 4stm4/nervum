"""``/v1/system/*`` — runtime stats (load average, uptime, ...)."""

from __future__ import annotations

from fastapi import APIRouter

from netos_agent.adapters.http_api.dependencies import GetSystemStatsDep
from netos_agent.adapters.http_api.schemas import SystemStatsOut

router = APIRouter(prefix="/v1/system", tags=["system"])


@router.get("/stats", response_model=SystemStatsOut, summary="Live system stats")
async def stats(get_stats: GetSystemStatsDep) -> SystemStatsOut:
    return SystemStatsOut.from_domain(await get_stats.execute())
