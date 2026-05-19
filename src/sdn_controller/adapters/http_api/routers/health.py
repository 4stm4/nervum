"""Health and version endpoints.

Три ручки, по одной на смысл:

* ``/health`` — historical alias для ``/livez`` (оставляем для
  backwards-compat, новые клиенты пусть ходят на ``/livez``).
* ``/livez`` — *liveness*: процесс живой и ASGI отвечает. Не лезет в
  БД, не проверяет внешние зависимости — kubelet'у этого хватает,
  чтобы решить, рестартовать ли pod.
* ``/readyz`` — *readiness*: контроллер готов обслуживать трафик.
  Здесь дёргается БД (через ``container.readiness_check``); при
  ошибке отдаём ``503 Service Unavailable`` — балансировщик
  выведет инстанс из ротации.
* ``/version`` — версия + commit, для smoke-чеков релиз-пайплайна.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from sdn_controller import __version__
from sdn_controller.adapters.http_api.schemas import HealthResponse, VersionResponse
from sdn_controller.app.container import Container

router = APIRouter(tags=["meta"])
_log = structlog.get_logger(__name__)


@router.get("/health", response_model=HealthResponse, summary="Liveness alias")
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/livez", response_model=HealthResponse, summary="Liveness probe")
async def livez() -> HealthResponse:
    return HealthResponse()


@router.get(
    "/readyz",
    summary="Readiness probe — пингует БД, возвращает 503 при отказе",
    responses={
        200: {"model": HealthResponse},
        503: {"description": "Контроллер не готов обслуживать трафик"},
    },
)
async def readyz(request: Request) -> JSONResponse:
    container: Container = request.app.state.container
    try:
        await container.readiness_check()
    except Exception as exc:
        _log.warning("readyz_check_failed", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "reason": str(exc)},
        )
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.get("/version", response_model=VersionResponse, summary="Controller version")
async def version() -> VersionResponse:
    return VersionResponse(version=__version__)
