"""``/webhooks`` — northbound subscription API (SDN-054).

* ``POST /webhooks`` — создаёт подписку, возвращает plaintext-секрет
  **ровно один раз** (после этого секрет нельзя получить заново — это
  требование пользователя, чтобы избежать утечки через GET).
* ``GET /webhooks``, ``GET /webhooks/{id}`` — список и одна подписка
  без plaintext'а.
* ``DELETE /webhooks/{id}`` — удалить подписку.

Доступ — admin (`webhook:write`/`webhook:read`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from sdn_controller.adapters.http_api.auth import require
from sdn_controller.adapters.http_api.dependencies import (
    CreateWebhookDep,
    DeleteWebhookDep,
    GetWebhookDep,
    ListWebhooksDep,
)
from sdn_controller.adapters.http_api.schemas import (
    WebhookSubscriptionCreateResponse,
    WebhookSubscriptionIn,
    WebhookSubscriptionListResponse,
    WebhookSubscriptionOut,
)
from sdn_controller.core.use_cases.webhooks import CreateWebhookCommand
from sdn_controller.core.value_objects.ids import WebhookSubscriptionId
from sdn_controller.core.value_objects.security import Permission

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "",
    response_model=WebhookSubscriptionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook subscription (returns plaintext secret once)",
    dependencies=[Depends(require(Permission.WEBHOOK_WRITE))],
)
async def create_webhook(
    payload: WebhookSubscriptionIn,
    use_case: CreateWebhookDep,
) -> WebhookSubscriptionCreateResponse:
    created = await use_case.execute(
        CreateWebhookCommand(
            target_url=payload.target_url,
            event_types=tuple(payload.event_types),
            description=payload.description,
            labels=dict(payload.labels),
        )
    )
    return WebhookSubscriptionCreateResponse(
        subscription=WebhookSubscriptionOut.from_domain(created.subscription),
        secret_plaintext=created.secret_plaintext,
    )


@router.get(
    "",
    response_model=WebhookSubscriptionListResponse,
    summary="List webhook subscriptions",
    dependencies=[Depends(require(Permission.WEBHOOK_READ))],
)
async def list_webhooks(
    use_case: ListWebhooksDep,
) -> WebhookSubscriptionListResponse:
    items = await use_case.execute()
    return WebhookSubscriptionListResponse(
        items=[WebhookSubscriptionOut.from_domain(s) for s in items],
    )


@router.get(
    "/{subscription_id}",
    response_model=WebhookSubscriptionOut,
    summary="Get a webhook subscription",
    dependencies=[Depends(require(Permission.WEBHOOK_READ))],
)
async def get_webhook(
    subscription_id: str,
    use_case: GetWebhookDep,
) -> WebhookSubscriptionOut:
    sub = await use_case.execute(WebhookSubscriptionId(subscription_id))
    return WebhookSubscriptionOut.from_domain(sub)


@router.delete(
    "/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook subscription",
    dependencies=[Depends(require(Permission.WEBHOOK_WRITE))],
)
async def delete_webhook(
    subscription_id: str,
    use_case: DeleteWebhookDep,
) -> Response:
    await use_case.execute(WebhookSubscriptionId(subscription_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
