"""Endpoints ``/topology`` и ``/drift`` (Milestone 8 — SDN-026/027).

Оба эндпоинта read-only и не дёргают агента: они работают по
закэшированному observed state. Это намеренно — UI/CLI могут опрашивать
их часто, не вызывая сетевого трафика к узлам. Когда оператор хочет
свежие данные — он зовёт ``POST /networks/{id}/apply``, который
прогоняет полный observe → diff → apply → verify и обновляет кэш.
"""

from __future__ import annotations

from fastapi import APIRouter

from sdn_controller.adapters.http_api.dependencies import (
    GetTopologyDep,
    ScanDriftDep,
)
from sdn_controller.adapters.http_api.schemas import (
    DriftReportResponse,
    TopologyResponse,
)

router = APIRouter(tags=["topology"])


@router.get(
    "/topology",
    response_model=TopologyResponse,
    summary="Снимок графа: узлы, сети, наблюдаемые мосты, рёбра",
)
async def get_topology(use_case: GetTopologyDep) -> TopologyResponse:
    snapshot = await use_case.execute()
    return TopologyResponse.from_domain(snapshot)


@router.get(
    "/drift",
    response_model=DriftReportResponse,
    summary="Сравнение desired vs cached observed по каждой сети/узлу",
)
async def scan_drift(use_case: ScanDriftDep) -> DriftReportResponse:
    report = await use_case.execute()
    return DriftReportResponse.from_domain(report)
