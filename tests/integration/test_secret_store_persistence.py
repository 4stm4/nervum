"""Webhook subscription переживает рестарт через ``FernetSecretStore``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.adapters.secret_store import generate_master_key
from sdn_controller.adapters.webhook import InMemoryWebhookSender, hmac_signature
from sdn_controller.app.config import Settings
from sdn_controller.app.container import Container, build_container
from tests.conftest import CountingIdFactory, FrozenClock, SequentialTokenFactory


def _settings(tmp_path: Path, key: str) -> Settings:
    return Settings(
        persistence="memory",
        log_level="WARNING",
        log_format="console",
        auth_enabled=False,
        webhooks_use_inmemory_sender=True,
        secret_store_backend="file",
        secret_store_path=str(tmp_path / "store.enc"),
        secret_store_key=key,
    )


@pytest.fixture
async def aclient_pair(
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    tmp_path: Path,
) -> AsyncIterator[tuple[Container, str]]:
    """Готовим первый Container и yield'им (container, master_key).

    Тест сам поднимет второй Container с тем же ключом, чтобы
    проверить «после рестарта»."""
    key = generate_master_key()
    settings = _settings(tmp_path, key)
    container = build_container(
        settings,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        agent=FakeAgent(clock=clock),
    )
    yield container, key


async def test_subscription_secret_survives_container_rebuild(
    aclient_pair: tuple[Container, str],
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    tmp_path: Path,
) -> None:
    container, key = aclient_pair
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)

    # 1) Создаём подписку через старый контейнер → плейнтекст на диск.
    async with httpx.AsyncClient(transport=transport, base_url="http://controller") as http:
        r = await http.post(
            "/api/v1/webhooks",
            json={"target_url": "https://example.org/hook", "event_types": ["*"]},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        sub_id = body["subscription"]["id"]
        secret_plaintext = body["secret_plaintext"]

    # 2) «Рестартуем» — строим новый Container, используя те же
    #    persistence-настройки и Fernet-ключ. webhook-subscription
    #    repo у нас in-memory, поэтому подписку надо положить руками,
    #    но secret должен лежать в FernetSecretStore на диске.
    settings = _settings(tmp_path, key)
    new_container = build_container(
        settings,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        agent=FakeAgent(clock=clock),
    )
    # Подписка в memory-репе свежего контейнера отсутствует — переносим:
    sub = await container.webhook_subscriptions_repo.get(sub_id)
    assert sub is not None
    await new_container.webhook_subscriptions_repo.save(sub)

    # 3) Secret должен достаться через signer_store нового контейнера.
    cached_secret = await new_container.signer_store.get(sub_id)
    assert cached_secret == secret_plaintext

    # 4) Прокидываем событие → dispatcher успешно подписывает и шлёт.
    await new_container.events.publish(
        event_type="network.created",
        resource_type="network",
        resource_id="net_1",
        payload={"name": "tenant"},
    )
    result = await new_container.dispatch_webhooks.execute()
    assert result.events_dispatched == 1

    sender = new_container.webhook_sender
    assert isinstance(sender, InMemoryWebhookSender)
    assert len(sender.calls) == 1
    call = sender.calls[0]
    expected = hmac_signature(secret_plaintext=secret_plaintext, body=call.body)
    assert call.signature_header == expected
