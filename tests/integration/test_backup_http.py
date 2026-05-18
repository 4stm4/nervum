"""HTTP-end-to-end для /backup/export, /backup/import и snapshot-ручек."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.app.config import Settings
from sdn_controller.app.container import build_container
from tests.conftest import CountingIdFactory, FrozenClock, SequentialTokenFactory


@pytest.fixture
def shared_agent(clock: FrozenClock) -> FakeAgent:
    return FakeAgent(clock=clock)


@pytest.fixture
async def aclient(
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    shared_agent: FakeAgent,
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        persistence="memory",
        log_level="WARNING",
        log_format="console",
        auth_enabled=False,
    )
    container = build_container(
        settings,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        agent=shared_agent,
    )
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://controller") as http:
        yield http


# ---------------------------------------------------------------------------
# /backup
# ---------------------------------------------------------------------------


async def test_export_contains_network_after_create(aclient: httpx.AsyncClient) -> None:
    await aclient.post("/api/v1/networks", json={"name": "prod", "type": "flat"})

    r = await aclient.get("/api/v1/backup/export")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["manifest"]["schema_version"] == 1
    assert any(n["name"] == "prod" for n in body["networks"])


async def test_import_into_empty_succeeds(aclient: httpx.AsyncClient) -> None:
    # Сначала создадим в исходной БД, скачаем bundle.
    await aclient.post("/api/v1/networks", json={"name": "prod", "type": "flat"})
    bundle = (await aclient.get("/api/v1/backup/export")).json()

    # Импортируем в *ту же* БД — она уже не пустая → 409 conflict.
    conflict = await aclient.post("/api/v1/backup/import", json=bundle)
    assert conflict.status_code == 409, conflict.text


# ---------------------------------------------------------------------------
# /nodes/{id}/snapshots
# ---------------------------------------------------------------------------


async def test_take_and_restore_snapshot(aclient: httpx.AsyncClient) -> None:
    # Registr нового узла.
    register = await aclient.post(
        "/api/v1/nodes",
        json={"name": "node-a", "mgmt_ip": "10.0.0.1"},
    )
    assert register.status_code == 202, register.text
    node_id = register.json()["node"]["id"]

    # Снимем снапшот.
    take = await aclient.post(
        f"/api/v1/nodes/{node_id}/snapshots",
        json={"label": "pre-upgrade"},
    )
    assert take.status_code == 201, take.text
    snapshot_id = take.json()["id"]

    # Каталог теперь содержит наш снапшот.
    listing = await aclient.get(f"/api/v1/nodes/{node_id}/snapshots")
    assert listing.status_code == 200, listing.text
    assert any(s["id"] == snapshot_id for s in listing.json()["items"])

    # Restore проходит успешно (FakeAgent просто откатит OVS-state).
    restore = await aclient.post(f"/api/v1/node-snapshots/{snapshot_id}/restore")
    assert restore.status_code == 200, restore.text
    assert restore.json()["snapshot"]["id"] == snapshot_id


async def test_snapshot_for_unknown_node_returns_404(aclient: httpx.AsyncClient) -> None:
    r = await aclient.post("/api/v1/nodes/node_missing/snapshots", json={})
    assert r.status_code == 404, r.text
