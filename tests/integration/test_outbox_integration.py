"""HTTP → outbox: mutating endpoints должны писать в outbox (SDN-055)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.netos_agent import FakeAgent
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


async def test_create_network_writes_outbox_event(
    app_and_container: tuple[httpx.AsyncClient, Container],
) -> None:
    client, container = app_and_container
    r = await client.post(
        "/api/v1/networks",
        json={"name": "tenant", "type": "vxlan", "vni": 10100},
    )
    assert r.status_code == 202, r.text

    events = await container.outbox_repo.list_since(since=0)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "network.created"
    assert ev.resource_type == "network"
    assert ev.resource_id == r.json()["network"]["id"]
    assert ev.payload["name"] == "tenant"
    assert ev.payload["intent_version"] == 1
    assert ev.event_id == 1


async def test_register_node_writes_outbox_event(
    app_and_container: tuple[httpx.AsyncClient, Container],
) -> None:
    client, container = app_and_container
    r = await client.post("/api/v1/nodes", json={"name": "node-a", "mgmt_ip": "10.0.0.1"})
    assert r.status_code == 202, r.text

    events = await container.outbox_repo.list_since(since=0)
    types = [e.event_type for e in events]
    assert "node.registered" in types


async def test_apply_network_writes_apply_event(
    app_and_container: tuple[httpx.AsyncClient, Container],
) -> None:
    client, container = app_and_container
    node_resp = await client.post("/api/v1/nodes", json={"name": "node-a", "mgmt_ip": "10.0.0.1"})
    node_id = node_resp.json()["node"]["id"]
    net_resp = await client.post(
        "/api/v1/networks",
        json={"name": "tenant", "type": "vxlan", "vni": 10100, "node_ids": [node_id]},
    )
    net_id = net_resp.json()["network"]["id"]

    apply_resp = await client.post(f"/api/v1/networks/{net_id}/apply")
    assert apply_resp.status_code == 202, apply_resp.text
    assert apply_resp.json()["operation"]["status"] == "succeeded"

    events = await container.outbox_repo.list_since(since=0)
    apply_events = [e for e in events if e.event_type == "network.applied"]
    assert len(apply_events) == 1
    assert apply_events[0].payload["ok"] is True
    assert apply_events[0].payload["node_count"] == 1


async def test_outbox_event_ids_are_monotonic(
    app_and_container: tuple[httpx.AsyncClient, Container],
) -> None:
    client, container = app_and_container
    await client.post("/api/v1/networks", json={"name": "a", "type": "vxlan", "vni": 10100})
    await client.post("/api/v1/networks", json={"name": "b", "type": "vxlan", "vni": 10101})

    events = await container.outbox_repo.list_since(since=0)
    ids = [e.event_id for e in events]
    assert ids == sorted(ids), "event_id должен быть монотонным"
    assert len(set(ids)) == len(ids), "event_id должен быть уникальным"
