"""Unit-тесты webhook entity + HMAC + DispatchWebhooks (SDN-054)."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import pytest

from sdn_controller.adapters.memory import (
    InMemoryOutboxRepository,
    InMemoryWebhookSubscriptionRepository,
)
from sdn_controller.adapters.secret_store import InMemorySecretStore
from sdn_controller.adapters.webhook import (
    InMemoryWebhookSender,
    hmac_signature,
    secret_hash,
)
from sdn_controller.core.entities import OutboxEvent, WebhookSubscription
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.use_cases.webhooks import (
    CreateWebhookCommand,
    CreateWebhookSubscription,
    DeleteWebhookSubscription,
    DispatchWebhooks,
    GetWebhookSubscription,
)
from sdn_controller.core.value_objects.enums import WebhookSubscriptionState
from sdn_controller.core.value_objects.errors import NotFoundError, ValidationError
from sdn_controller.core.value_objects.ids import OutboxEventId, WebhookSubscriptionId
from tests.conftest import CountingIdFactory, FrozenClock

_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Entity invariants
# ---------------------------------------------------------------------------


def test_subscription_rejects_invalid_url() -> None:
    with pytest.raises(ValidationError, match="target_url"):
        WebhookSubscription(
            id=WebhookSubscriptionId("whsub_1"),
            target_url="ftp://oops",
            secret_hash="x",
            event_types=("*",),
            state=WebhookSubscriptionState.ACTIVE,
            created_at=_NOW,
            updated_at=_NOW,
        )


def test_subscription_rejects_invalid_event_type() -> None:
    with pytest.raises(ValidationError, match="event_type"):
        WebhookSubscription(
            id=WebhookSubscriptionId("whsub_1"),
            target_url="https://example.org/hook",
            secret_hash="x",
            event_types=("no_dot",),
            state=WebhookSubscriptionState.ACTIVE,
            created_at=_NOW,
            updated_at=_NOW,
        )


def test_subscription_matches_wildcard_and_exact() -> None:
    star = WebhookSubscription(
        id=WebhookSubscriptionId("whsub_a"),
        target_url="https://example.org/hook",
        secret_hash="x",
        event_types=("*",),
        state=WebhookSubscriptionState.ACTIVE,
        created_at=_NOW,
        updated_at=_NOW,
    )
    explicit = WebhookSubscription(
        id=WebhookSubscriptionId("whsub_b"),
        target_url="https://example.org/hook",
        secret_hash="x",
        event_types=("network.created",),
        state=WebhookSubscriptionState.ACTIVE,
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert star.matches("anything.happened") is True
    assert explicit.matches("network.created") is True
    assert explicit.matches("node.registered") is False


# ---------------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------------


def test_hmac_signature_matches_reference() -> None:
    body = b'{"hello":"world"}'
    secret = "topsecret"
    expected_digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert hmac_signature(secret_plaintext=secret, body=body) == f"sha256={expected_digest}"


def test_secret_hash_is_deterministic() -> None:
    assert secret_hash("abc") == secret_hash("abc")
    assert secret_hash("abc") != secret_hash("abd")


# ---------------------------------------------------------------------------
# CRUD use cases
# ---------------------------------------------------------------------------


async def test_create_returns_plaintext_once(
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> None:
    subs = InMemoryWebhookSubscriptionRepository()
    outbox = InMemoryOutboxRepository()
    signer = InMemorySecretStore()
    use_case = CreateWebhookSubscription(
        subscriptions=subs,
        outbox=outbox,
        signer_store=signer,
        clock=clock,
        ids=ids,
    )
    created = await use_case.execute(
        CreateWebhookCommand(
            target_url="https://example.org/hook",
            event_types=("network.created",),
        )
    )
    assert len(created.secret_plaintext) > 16
    persisted = await subs.get(created.subscription.id)
    assert persisted is not None
    assert persisted.secret_hash == secret_hash(created.secret_plaintext)
    assert await signer.get(created.subscription.id) == created.secret_plaintext


async def test_create_starts_cursor_at_outbox_head(
    clock: FrozenClock,
    ids: CountingIdFactory,
    events: EventPublisher,
) -> None:
    subs = InMemoryWebhookSubscriptionRepository()
    outbox = InMemoryOutboxRepository()
    # 2 события до создания подписки — подписка не должна их получить.
    publisher = EventPublisher(outbox=outbox, clock=clock, ids=ids)
    await publisher.publish(
        event_type="network.created", resource_type="network", resource_id="net_1"
    )
    await publisher.publish(
        event_type="network.updated", resource_type="network", resource_id="net_1"
    )

    use_case = CreateWebhookSubscription(
        subscriptions=subs,
        outbox=outbox,
        signer_store=InMemorySecretStore(),
        clock=clock,
        ids=ids,
    )
    created = await use_case.execute(
        CreateWebhookCommand(target_url="https://example.org/hook", event_types=("*",))
    )
    assert created.subscription.cursor == 2  # head после двух событий


async def test_delete_forgets_secret(
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> None:
    subs = InMemoryWebhookSubscriptionRepository()
    signer = InMemorySecretStore()
    create = CreateWebhookSubscription(
        subscriptions=subs,
        outbox=InMemoryOutboxRepository(),
        signer_store=signer,
        clock=clock,
        ids=ids,
    )
    created = await create.execute(
        CreateWebhookCommand(target_url="https://example.org/hook", event_types=("*",))
    )

    delete = DeleteWebhookSubscription(subscriptions=subs, signer_store=signer)
    await delete.execute(created.subscription.id)

    assert await subs.get(created.subscription.id) is None
    assert await signer.get(created.subscription.id) is None


async def test_get_unknown_raises_not_found() -> None:
    use_case = GetWebhookSubscription(subscriptions=InMemoryWebhookSubscriptionRepository())
    with pytest.raises(NotFoundError):
        await use_case.execute(WebhookSubscriptionId("whsub_missing"))


# ---------------------------------------------------------------------------
# DispatchWebhooks
# ---------------------------------------------------------------------------


def _event(event_id: int, event_type: str = "network.created") -> OutboxEvent:
    return OutboxEvent(
        id=OutboxEventId(f"outbox_{event_id}"),
        event_id=event_id,
        occurred_at=_NOW,
        event_type=event_type,
        resource_type="network",
        resource_id="net_1",
        payload={"i": event_id},
    )


async def test_dispatch_delivers_pending_events(
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> None:
    subs = InMemoryWebhookSubscriptionRepository()
    outbox = InMemoryOutboxRepository()
    signer = InMemorySecretStore()
    sender = InMemoryWebhookSender()

    # Subscription уже есть, cursor=0.
    create = CreateWebhookSubscription(
        subscriptions=subs,
        outbox=outbox,
        signer_store=signer,
        clock=clock,
        ids=ids,
    )
    created = await create.execute(
        CreateWebhookCommand(target_url="https://example.org/hook", event_types=("*",))
    )
    # 3 события появляются ПОСЛЕ создания.
    publisher = EventPublisher(outbox=outbox, clock=clock, ids=ids)
    await publisher.publish(
        event_type="network.created", resource_type="network", resource_id="net_1"
    )
    await publisher.publish(
        event_type="node.registered", resource_type="node", resource_id="node_1"
    )
    await publisher.publish(
        event_type="network.applied", resource_type="network", resource_id="net_1"
    )

    dispatcher = DispatchWebhooks(
        subscriptions=subs,
        outbox=outbox,
        sender=sender,
        signer_store=signer,
        clock=clock,
    )
    result = await dispatcher.execute()

    assert result.events_dispatched == 3
    assert len(sender.calls) == 3
    persisted = await subs.get(created.subscription.id)
    assert persisted is not None
    assert persisted.cursor == 3
    assert persisted.failure_count == 0


async def test_dispatch_advances_cursor_for_filtered_events(
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> None:
    subs = InMemoryWebhookSubscriptionRepository()
    outbox = InMemoryOutboxRepository()
    signer = InMemorySecretStore()
    sender = InMemoryWebhookSender()

    create = CreateWebhookSubscription(
        subscriptions=subs,
        outbox=outbox,
        signer_store=signer,
        clock=clock,
        ids=ids,
    )
    created = await create.execute(
        CreateWebhookCommand(
            target_url="https://example.org/hook",
            event_types=("network.applied",),
        )
    )

    publisher = EventPublisher(outbox=outbox, clock=clock, ids=ids)
    await publisher.publish(
        event_type="network.created", resource_type="network", resource_id="net_1"
    )
    await publisher.publish(
        event_type="network.applied", resource_type="network", resource_id="net_1"
    )

    dispatcher = DispatchWebhooks(
        subscriptions=subs,
        outbox=outbox,
        sender=sender,
        signer_store=signer,
        clock=clock,
    )
    result = await dispatcher.execute()

    assert result.events_dispatched == 1
    assert [c.event_type for c in sender.calls] == ["network.applied"]
    persisted = await subs.get(created.subscription.id)
    assert persisted is not None
    assert persisted.cursor == 2  # обе обработаны (одна skipped, одна sent)


async def test_dispatch_disables_after_max_failures(
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> None:
    subs = InMemoryWebhookSubscriptionRepository()
    outbox = InMemoryOutboxRepository()
    signer = InMemorySecretStore()
    sender = InMemoryWebhookSender(fail_for_urls={"https://example.org/hook"})

    create = CreateWebhookSubscription(
        subscriptions=subs,
        outbox=outbox,
        signer_store=signer,
        clock=clock,
        ids=ids,
    )
    created = await create.execute(
        CreateWebhookCommand(target_url="https://example.org/hook", event_types=("*",))
    )

    publisher = EventPublisher(outbox=outbox, clock=clock, ids=ids)
    for _ in range(3):
        await publisher.publish(
            event_type="network.created",
            resource_type="network",
            resource_id="net_x",
        )

    dispatcher = DispatchWebhooks(
        subscriptions=subs,
        outbox=outbox,
        sender=sender,
        signer_store=signer,
        clock=clock,
        max_failures=2,
    )
    # Каждый tick поднимает failure_count на 1 (мы прерываем подписку
    # после первого failure).
    await dispatcher.execute()
    await dispatcher.execute()

    persisted = await subs.get(created.subscription.id)
    assert persisted is not None
    assert persisted.state is WebhookSubscriptionState.DISABLED
    assert persisted.failure_count >= 2


async def test_dispatch_disables_when_secret_unavailable(
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> None:
    subs = InMemoryWebhookSubscriptionRepository()
    sub = WebhookSubscription(
        id=WebhookSubscriptionId("whsub_1"),
        target_url="https://example.org/hook",
        secret_hash="dead",
        event_types=("*",),
        state=WebhookSubscriptionState.ACTIVE,
        created_at=_NOW,
        updated_at=_NOW,
    )
    await subs.save(sub)
    outbox = InMemoryOutboxRepository()
    sender = InMemoryWebhookSender()

    dispatcher = DispatchWebhooks(
        subscriptions=subs,
        outbox=outbox,
        sender=sender,
        signer_store=InMemorySecretStore(),  # пусто
        clock=clock,
    )
    result = await dispatcher.execute()
    assert result.disabled_subscriptions == 1
    persisted = await subs.get(sub.id)
    assert persisted is not None
    assert persisted.state is WebhookSubscriptionState.DISABLED
