"""HTTP-тесты ``/events/snapshot`` + ``/events`` (SDN-057)."""

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
async def aclient(
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
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
        agent=FakeAgent(clock=clock),
    )
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://controller") as http:
        yield http


async def test_snapshot_returns_state_and_watermark(
    aclient: httpx.AsyncClient,
) -> None:
    # Создаём пару объектов — снапшот должен их вернуть.
    await aclient.post("/api/v1/nodes", json={"name": "node-a", "mgmt_ip": "10.0.0.1"})
    await aclient.post("/api/v1/networks", json={"name": "tenant", "type": "vxlan", "vni": 10100})

    r = await aclient.get("/api/v1/events/snapshot")
    assert r.status_code == 200, r.text
    body = r.json()
    # event_id монотонный, ровно 2 события произошло.
    assert body["event_id"] == 2
    assert [n["name"] for n in body["nodes"]] == ["node-a"]
    assert [n["name"] for n in body["networks"]] == ["tenant"]


async def test_events_paginate_since_watermark(
    aclient: httpx.AsyncClient,
) -> None:
    await aclient.post("/api/v1/nodes", json={"name": "node-a", "mgmt_ip": "10.0.0.1"})
    snap = await aclient.get("/api/v1/events/snapshot")
    watermark = snap.json()["event_id"]

    # Действие после snapshot'а — попадёт в delta.
    await aclient.post("/api/v1/networks", json={"name": "tenant", "type": "vxlan", "vni": 10100})

    r = await aclient.get(f"/api/v1/events?since={watermark}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["head_event_id"] == watermark + 1
    assert len(body["items"]) == 1
    assert body["items"][0]["event_type"] == "network.created"
    assert body["items"][0]["event_id"] == watermark + 1


async def test_events_empty_when_caught_up(
    aclient: httpx.AsyncClient,
) -> None:
    await aclient.post("/api/v1/nodes", json={"name": "node-a", "mgmt_ip": "10.0.0.1"})
    snap = await aclient.get("/api/v1/events/snapshot")
    watermark = snap.json()["event_id"]

    r = await aclient.get(f"/api/v1/events?since={watermark}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["head_event_id"] == watermark


async def test_events_respect_limit(
    aclient: httpx.AsyncClient,
) -> None:
    for i in range(5):
        await aclient.post(
            "/api/v1/networks",
            json={"name": f"n{i}", "type": "vxlan", "vni": 10100 + i},
        )

    r = await aclient.get("/api/v1/events?limit=3")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 3
    assert body["head_event_id"] == 5
