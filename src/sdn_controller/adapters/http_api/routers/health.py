"""Health and version endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from sdn_controller import __version__
from sdn_controller.adapters.http_api.schemas import HealthResponse, VersionResponse

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/version", response_model=VersionResponse, summary="Controller version")
async def version() -> VersionResponse:
    return VersionResponse(version=__version__)
