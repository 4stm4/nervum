"""Endpoints export/import bundle'а (M11 — SDN-034).

``GET /backup/export`` отдаёт текущее состояние JSON'ом, который
оператор может сохранить и потом загрузить через
``POST /backup/import``. На время migration'а БД — это наш единственный
надёжный path-around: даже если схема SQLite сломается, bundle
останется живым.

Доступ — два отдельных permission'а: ``backup:export`` и
``backup:import``. Импорт намного опаснее (он переписывает БД), поэтому
у него собственный гейт; даже admin не может случайно его дёрнуть из
viewer-токена.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends

from sdn_controller.adapters.http_api.auth import require
from sdn_controller.adapters.http_api.dependencies import (
    ExportBundleDep,
    ImportBundleDep,
)
from sdn_controller.adapters.http_api.schemas import BundleImportResponse
from sdn_controller.core.use_cases.backup import (
    bundle_from_dict,
    bundle_to_dict,
)
from sdn_controller.core.value_objects.security import Permission

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get(
    "/export",
    summary="Снимок состояния (JSON-bundle)",
    dependencies=[Depends(require(Permission.BACKUP_EXPORT))],
)
async def export_bundle(use_case: ExportBundleDep) -> dict[str, Any]:
    bundle = await use_case.execute()
    return bundle_to_dict(bundle)


@router.post(
    "/import",
    response_model=BundleImportResponse,
    summary="Восстановить состояние из JSON-bundle'а (НЕ merge — только в пустую БД)",
    dependencies=[Depends(require(Permission.BACKUP_IMPORT))],
)
async def import_bundle(
    use_case: ImportBundleDep,
    payload: Annotated[dict[str, Any], Body()],
) -> BundleImportResponse:
    bundle = bundle_from_dict(payload)
    summary = await use_case.execute(bundle)
    return BundleImportResponse(
        networks=summary.networks,
        nodes=summary.nodes,
        service_accounts=summary.service_accounts,
        ip_allocations=summary.ip_allocations,
        audit_events=summary.audit_events,
    )
