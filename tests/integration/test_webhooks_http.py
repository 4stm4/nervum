"""HTTP-CRUD для ``/webhooks`` + сквозной dispatcher (SDN-054)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.adapters.webhook import InMemoryWebhookSender, hmac_signature
from sdn_controller.app.config import Settings
from sdn_controller.app.container import Container, build_container
from tests.conftest import CountingIdFactory, FrozenClock, SequentialTokenFactory


@pytest.fixture
async def app_and_container(
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
) -> AsyncIterator[tuple[httpx.AsyncClient, Container]]:
    settings = Settings(
        persistence="memory",
        log_level="WARNING",
        log_format="console",
        auth_enabled=False,
        webhooks_use_inmemory_sender=True,
    )
    container = build_container(
        settings,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        agent=FakeAgent(clock=clock),
    )
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://controller") as http:
        yield http, container


async def test_create_returns_plaintext_secret_once(
    app_and_container: tuple[httpx.AsyncClient, Container],
) -> None:
    client, _ = app_and_container
    r = await client.post(
        "/api/v1/webhooks",
        json={
            "target_url": "https://example.org/hook",
            "event_types": ["network.created", "network.applied"],
            "description": "testum-bridge",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["subscription"]["target_url"] == "https://example.org/hook"
    assert body["subscription"]["event_types"] == ["network.created", "network.applied"]
    assert isinstance(body["secret_plaintext"], str)
    assert len(body["secret_plaintext"]) > 16


async def test_list_does_not_return_secret(
    app_and_container: tuple[httpx.AsyncClient, Container],
) -> None:
    client, _ = app_and_container
    await client.post(
        "/api/v1/webhooks",
        json={"target_url": "https://example.org/hook", "event_types": ["*"]},
    )
    r = await client.get("/api/v1/webhooks")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert "secret_plaintext" not in items[0]
    assert "secret_hash" not in items[0]


async def test_delete_removes_subscription(
    app_and_container: tuple[httpx.AsyncClient, Container],
) -> None:
    client, _ = app_and_container
    created = await client.post(
        "/api/v1/webhooks",
        json={"target_url": "https://example.org/hook", "event_types": ["*"]},
    )
    sub_id = created.json()["subscription"]["id"]

    r = await client.delete(f"/api/v1/webhooks/{sub_id}")
    assert r.status_code == 204, r.text

    after = await client.get(f"/api/v1/webhooks/{sub_id}")
    assert after.status_code == 404


async def test_dispatch_delivers_event_with_valid_hmac(
    app_and_container: tuple[httpx.AsyncClient, Container],
) -> None:
    """Сквозной сценарий: создаём подписку, генерим событие, дёргаем
    dispatcher.execute() напрямую — проверяем HMAC и тело."""
    client, container = app_and_container

    created = await client.post(
        "/api/v1/webhooks",
        json={"target_url": "https://example.org/hook", "event_types": ["*"]},
    )
    secret = created.json()["secret_plaintext"]

    # POST /networks → пишет в outbox network.created
    await client.post("/api/v1/networks", json={"name": "tenant", "type": "vxlan", "vni": 10100})

    # Прокидываем dispatcher вручную (background-loop в тестах не запущен).
    result = await container.dispatch_webhooks.execute()
    assert result.events_dispatched == 1

    sender = container.webhook_sender
    assert isinstance(sender, InMemoryWebhookSender)
    assert len(sender.calls) == 1
    call = sender.calls[0]
    assert call.event_type == "network.created"
    # HMAC должен совпадать с независимым подсчётом.
    expected = hmac_signature(secret_plaintext=secret, body=call.body)
    assert call.signature_header == expected
