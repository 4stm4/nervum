"""Use cases для webhook subscriptions (SDN-054).

CRUD + диспатч цикла:

* ``CreateWebhookSubscription`` — генерирует secret_plaintext (через
  ``TokenFactory``), сохраняет hash, кладёт plaintext в ``SignerStore``
  для in-process диспатчера. Возвращает plaintext **ровно один раз**.
* ``ListWebhookSubscriptions``/``GetWebhookSubscription`` —
  read-side; plaintext не возвращают, только публичные поля.
* ``DeleteWebhookSubscription`` — удаляет подписку и stripит cached
  plaintext.
* ``DispatchWebhooks`` — background-цикл, читает outbox по cursor'у
  каждой подписки, отдаёт по одному событию через ``WebhookSender``.

Доставка at-least-once: cursor продвигается только после успеха.
В случае подряд ``max_failures`` неудач подписка переводится в
``disabled`` (operator увидит в /webhooks и решит, что делать).
"""

from __future__ import annotations

import json
import secrets as _secrets
from dataclasses import dataclass, field

import structlog

from sdn_controller.adapters.webhook import (
    hmac_signature,
    secret_hash as _secret_hash,
)
from sdn_controller.app.tracing import tracer
from sdn_controller.core.entities import OutboxEvent, WebhookSubscription
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.value_objects.enums import WebhookSubscriptionState
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import IdFactory, WebhookSubscriptionId
from sdn_controller.ports.persistence import (
    OutboxRepository,
    WebhookSubscriptionRepository,
)
from sdn_controller.ports.secret_store import SecretStore
from sdn_controller.ports.webhook_sender import WebhookDelivery, WebhookSender

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Commands & results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateWebhookCommand:
    target_url: str
    event_types: tuple[str, ...]
    description: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedWebhook:
    subscription: WebhookSubscription
    secret_plaintext: str  # ровно один раз!


@dataclass(frozen=True, slots=True)
class DispatchWebhooksResult:
    subscriptions_checked: int
    events_dispatched: int
    failures: int
    disabled_subscriptions: int


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


class CreateWebhookSubscription:
    def __init__(
        self,
        *,
        subscriptions: WebhookSubscriptionRepository,
        outbox: OutboxRepository,
        signer_store: SecretStore,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._subscriptions = subscriptions
        self._outbox = outbox
        self._signer_store = signer_store
        self._clock = clock
        self._ids = ids

    async def execute(self, cmd: CreateWebhookCommand) -> CreatedWebhook:
        now = self._clock.now()
        plaintext = _secrets.token_urlsafe(32)
        sub_id = self._ids.webhook_subscription()
        # Стартуем cursor с head outbox'а: оператор не хочет, чтобы
        # новая подписка получила «весь архив» — обычно нужна только
        # дельта с момента подписки.
        head = await self._outbox.head_event_id()
        subscription = WebhookSubscription(
            id=sub_id,
            target_url=cmd.target_url,
            secret_hash=_secret_hash(plaintext),
            event_types=tuple(cmd.event_types),
            state=WebhookSubscriptionState.ACTIVE,
            created_at=now,
            updated_at=now,
            cursor=head,
            description=cmd.description,
            labels=dict(cmd.labels),
        )
        await self._subscriptions.save(subscription)
        await self._signer_store.remember(sub_id, plaintext)
        return CreatedWebhook(subscription=subscription, secret_plaintext=plaintext)


class ListWebhookSubscriptions:
    def __init__(self, *, subscriptions: WebhookSubscriptionRepository) -> None:
        self._subscriptions = subscriptions

    async def execute(self) -> list[WebhookSubscription]:
        return list(await self._subscriptions.list())


class GetWebhookSubscription:
    def __init__(self, *, subscriptions: WebhookSubscriptionRepository) -> None:
        self._subscriptions = subscriptions

    async def execute(self, sub_id: WebhookSubscriptionId) -> WebhookSubscription:
        sub = await self._subscriptions.get(sub_id)
        if sub is None:
            raise NotFoundError(f"webhook subscription {sub_id} not found")
        return sub


class DeleteWebhookSubscription:
    def __init__(
        self,
        *,
        subscriptions: WebhookSubscriptionRepository,
        signer_store: SecretStore,
    ) -> None:
        self._subscriptions = subscriptions
        self._signer_store = signer_store

    async def execute(self, sub_id: WebhookSubscriptionId) -> None:
        sub = await self._subscriptions.get(sub_id)
        if sub is None:
            raise NotFoundError(f"webhook subscription {sub_id} not found")
        await self._subscriptions.delete(sub_id)
        await self._signer_store.forget(sub_id)


class DispatchWebhooks:
    """Один проход «outbox → active subscriptions → POST».

    Для каждой active подписки берёт пачку событий с ``event_id >
    cursor``, фильтрует по ``event_types`` и отдаёт sender'у. На
    каждое событие — отдельный HTTP-вызов; cursor двигается только
    после ok-ответа.
    """

    def __init__(
        self,
        *,
        subscriptions: WebhookSubscriptionRepository,
        outbox: OutboxRepository,
        sender: WebhookSender,
        signer_store: SecretStore,
        clock: Clock,
        batch_size: int = 50,
        max_failures: int = 10,
    ) -> None:
        self._subscriptions = subscriptions
        self._outbox = outbox
        self._sender = sender
        self._signer_store = signer_store
        self._clock = clock
        self._batch_size = batch_size
        self._max_failures = max_failures

    async def execute(self) -> DispatchWebhooksResult:
        active = await self._subscriptions.list_active()
        dispatched = 0
        failures = 0
        disabled = 0
        for sub in active:
            secret = await self._signer_store.get(sub.id)
            if secret is None:
                # Plaintext недоступен (например, после рестарта без
                # SecretStore). Подписку выключаем, чтобы оператор
                # увидел и rotate'нул.
                sub.disable(at=self._clock.now(), reason="secret unavailable in process cache")
                await self._subscriptions.save(sub)
                disabled += 1
                continue

            events = await self._outbox.list_since(since=sub.cursor, limit=self._batch_size)
            for event in events:
                if not sub.matches(event.event_type):
                    # Не наш тип, но cursor двинуть надо, иначе мы
                    # будем перечитывать его каждый tick.
                    sub.cursor = max(sub.cursor, event.event_id)
                    sub.updated_at = self._clock.now()
                    continue

                delivery = _build_delivery(event=event, secret=secret, target_url=sub.target_url)
                with tracer().start_as_current_span(
                    "sdn.webhook.deliver",
                    attributes={
                        "sdn.subscription_id": sub.id,
                        "sdn.target_url": sub.target_url,
                        "sdn.event_id": event.event_id,
                        "sdn.event_type": event.event_type,
                        "sdn.delivery_id": delivery.delivery_id,
                    },
                ) as span:
                    result = await self._sender.send(delivery)
                    span.set_attribute("sdn.delivery_ok", result.ok)
                    if result.http_status is not None:
                        span.set_attribute("http.status_code", result.http_status)
                if result.ok:
                    sub.mark_delivered(event_id=event.event_id, at=self._clock.now())
                    dispatched += 1
                else:
                    sub.mark_failed(
                        at=self._clock.now(),
                        error=result.error or "unknown",
                    )
                    failures += 1
                    if sub.failure_count >= self._max_failures:
                        sub.disable(
                            at=self._clock.now(),
                            reason=f"{sub.failure_count} consecutive failures",
                        )
                        disabled += 1
                    # На failure прерываем процессинг этой подписки —
                    # следующая попытка случится на след. tick'е.
                    break

            await self._subscriptions.save(sub)

        _log.info(
            "webhook_dispatch_tick",
            subscriptions_checked=len(active),
            events_dispatched=dispatched,
            failures=failures,
            disabled_subscriptions=disabled,
        )
        return DispatchWebhooksResult(
            subscriptions_checked=len(active),
            events_dispatched=dispatched,
            failures=failures,
            disabled_subscriptions=disabled,
        )


def _build_delivery(*, event: OutboxEvent, secret: str, target_url: str) -> WebhookDelivery:
    """Канонический body для подписи и доставки."""
    body_obj = {
        "event_id": event.event_id,
        "id": event.id,
        "event_type": event.event_type,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": event.payload,
    }
    body = json.dumps(body_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return WebhookDelivery(
        target_url=target_url,
        event_id=event.event_id,
        event_type=event.event_type,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        body=body,
        signature_header=hmac_signature(secret_plaintext=secret, body=body),
        delivery_id=_secrets.token_hex(8),
    )
