"""Endpoints управления сервисными аккаунтами и их токенами (M9 — SDN-028/030).

Доступ — только admin: эти ручки выпускают и отзывают аутентификационные
секреты, и любая лишняя возможность видеть чужие токены — это лестница
для горизонтального движения злоумышленника.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from sdn_controller.adapters.http_api.auth import CurrentPrincipal, require
from sdn_controller.adapters.http_api.dependencies import (
    CreateServiceAccountDep,
    DisableServiceAccountDep,
    GetServiceAccountDep,
    IssueServiceTokenDep,
    ListServiceAccountsDep,
    ListServiceTokensDep,
    RevokeServiceTokenDep,
)
from sdn_controller.adapters.http_api.schemas import (
    ServiceAccountCreateRequest,
    ServiceAccountListResponse,
    ServiceAccountOut,
    ServiceTokenIssueRequest,
    ServiceTokenIssueResponse,
    ServiceTokenListResponse,
    ServiceTokenOut,
)
from sdn_controller.core.use_cases.service_accounts import (
    CreateServiceAccountCommand,
    IssueServiceTokenCommand,
)
from sdn_controller.core.value_objects.ids import ServiceAccountId, ServiceTokenId
from sdn_controller.core.value_objects.security import Permission

accounts_router = APIRouter(
    prefix="/service-accounts",
    tags=["security"],
    dependencies=[Depends(require(Permission.SERVICE_ACCOUNT_READ))],
)
tokens_router = APIRouter(
    prefix="/service-tokens",
    tags=["security"],
    dependencies=[Depends(require(Permission.SERVICE_TOKEN_READ))],
)


# ---------------------------------------------------------------------------
# Service accounts
# ---------------------------------------------------------------------------


@accounts_router.get(
    "",
    response_model=ServiceAccountListResponse,
    summary="Список сервисных аккаунтов",
)
async def list_service_accounts(
    use_case: ListServiceAccountsDep,
) -> ServiceAccountListResponse:
    accounts = await use_case.execute()
    return ServiceAccountListResponse(items=[ServiceAccountOut.from_domain(a) for a in accounts])


@accounts_router.post(
    "",
    response_model=ServiceAccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать сервисный аккаунт",
    dependencies=[Depends(require(Permission.SERVICE_ACCOUNT_WRITE))],
)
async def create_service_account(
    payload: ServiceAccountCreateRequest,
    principal: CurrentPrincipal,
    use_case: CreateServiceAccountDep,
) -> ServiceAccountOut:
    account = await use_case.execute(
        CreateServiceAccountCommand(
            name=payload.name,
            role=payload.role,
            description=payload.description,
            labels=dict(payload.labels),
            created_by=principal.name,
        )
    )
    return ServiceAccountOut.from_domain(account)


@accounts_router.get(
    "/{account_id}",
    response_model=ServiceAccountOut,
    summary="Получить сервисный аккаунт",
)
async def get_service_account(
    account_id: str,
    use_case: GetServiceAccountDep,
) -> ServiceAccountOut:
    account = await use_case.execute(ServiceAccountId(account_id))
    return ServiceAccountOut.from_domain(account)


@accounts_router.post(
    "/{account_id}/disable",
    response_model=ServiceAccountOut,
    summary="Заблокировать сервисный аккаунт",
    dependencies=[Depends(require(Permission.SERVICE_ACCOUNT_WRITE))],
)
async def disable_service_account(
    account_id: str,
    use_case: DisableServiceAccountDep,
) -> ServiceAccountOut:
    account = await use_case.execute(ServiceAccountId(account_id))
    return ServiceAccountOut.from_domain(account)


@accounts_router.get(
    "/{account_id}/tokens",
    response_model=ServiceTokenListResponse,
    summary="Список токенов сервисного аккаунта",
)
async def list_service_tokens(
    account_id: str,
    use_case: ListServiceTokensDep,
) -> ServiceTokenListResponse:
    tokens = await use_case.execute(ServiceAccountId(account_id))
    return ServiceTokenListResponse(items=[ServiceTokenOut.from_domain(t) for t in tokens])


@accounts_router.post(
    "/{account_id}/tokens",
    response_model=ServiceTokenIssueResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Выпустить новый токен сервисному аккаунту",
    dependencies=[Depends(require(Permission.SERVICE_TOKEN_WRITE))],
)
async def issue_service_token(
    account_id: str,
    payload: ServiceTokenIssueRequest,
    principal: CurrentPrincipal,
    use_case: IssueServiceTokenDep,
) -> ServiceTokenIssueResponse:
    issued = await use_case.execute(
        IssueServiceTokenCommand(
            account_id=ServiceAccountId(account_id),
            ttl_seconds=payload.ttl_seconds,
            label=payload.label,
            issued_by=principal.name,
        )
    )
    return ServiceTokenIssueResponse(
        token=ServiceTokenOut.from_domain(issued.token),
        plaintext=issued.plaintext,
    )


# ---------------------------------------------------------------------------
# Service tokens (revoke by id)
# ---------------------------------------------------------------------------


@tokens_router.post(
    "/{token_id}/revoke",
    response_model=ServiceTokenOut,
    summary="Отозвать токен (идемпотентно)",
    dependencies=[Depends(require(Permission.SERVICE_TOKEN_WRITE))],
)
async def revoke_service_token(
    token_id: str,
    use_case: RevokeServiceTokenDep,
) -> ServiceTokenOut:
    token = await use_case.execute(ServiceTokenId(token_id))
    return ServiceTokenOut.from_domain(token)
