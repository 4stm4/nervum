"""Integration: ``/metrics``, ``X-Request-Id``, ``/audit-events`` + middleware."""

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
# X-Request-Id
# ---------------------------------------------------------------------------


async def test_request_id_is_returned_when_missing(aclient: httpx.AsyncClient) -> None:
    r = await aclient.get("/api/v1/health")
    assert r.status_code == 200, r.text
    rid = r.headers.get("x-request-id")
    assert rid
    assert len(rid) >= 16  # uuid hex


async def test_request_id_is_echoed_when_supplied(aclient: httpx.AsyncClient) -> None:
    r = await aclient.get(
        "/api/v1/health",
        headers={"x-request-id": "client-correlation-1"},
    )
    assert r.headers["x-request-id"] == "client-correlation-1"


# ---------------------------------------------------------------------------
# /metrics
# ---------------------------------------------------------------------------


async def test_metrics_endpoint_exposes_counters(aclient: httpx.AsyncClient) -> None:
    # Сгенерим хотя бы один запрос, чтобы счётчик был не-нулевым.
    await aclient.get("/api/v1/health")
    r = await aclient.get("/metrics")
    assert r.status_code == 200, r.text
    body = r.text
    assert "sdn_http_requests_total" in body
    assert "sdn_http_request_duration_seconds" in body
    # ``/metrics`` сам себя не считает на этом шаге, но запрос /health
    # уже должен был отметиться.
    assert 'path="/api/v1/health"' in body


# ---------------------------------------------------------------------------
# /audit-events + middleware
# ---------------------------------------------------------------------------


async def test_audit_event_recorded_on_create_network(aclient: httpx.AsyncClient) -> None:
    create = await aclient.post(
        "/api/v1/networks",
        json={"name": "ops-net", "type": "flat"},
    )
    assert create.status_code == 202, create.text

    events = (await aclient.get("/api/v1/audit-events")).json()["items"]
    actions = [it["action"] for it in events]
    assert "network.create" in actions
    network_create = next(it for it in events if it["action"] == "network.create")
    assert network_create["resource_type"] == "network"
    assert network_create["http_status"] == 202
    # request_id присутствует в каждой записи.
    assert network_create["request_id"]


async def test_audit_event_records_resource_id_for_patch(
    aclient: httpx.AsyncClient,
) -> None:
    created = (await aclient.post("/api/v1/networks", json={"name": "n", "type": "flat"})).json()
    network_id = created["network"]["id"]

    await aclient.patch(f"/api/v1/networks/{network_id}", json={"mtu": 1450})

    items = (await aclient.get("/api/v1/audit-events")).json()["items"]
    update_event = next(it for it in items if it["action"] == "network.update")
    assert update_event["resource_id"] == network_id


async def test_audit_event_skipped_for_pure_read(aclient: httpx.AsyncClient) -> None:
    await aclient.get("/api/v1/networks")
    items = (await aclient.get("/api/v1/audit-events")).json()["items"]
    actions = [it["action"] for it in items]
    # Никаких "network.read" — мы аудитим только mutating.
    assert "network.read" not in actions


async def test_audit_filters_by_action_and_resource(aclient: httpx.AsyncClient) -> None:
    await aclient.post("/api/v1/networks", json={"name": "a", "type": "flat"})
    await aclient.post("/api/v1/networks", json={"name": "b", "type": "flat"})

    only_creates = (
        await aclient.get(
            "/api/v1/audit-events",
            params={"action": "network.create"},
        )
    ).json()["items"]
    assert len(only_creates) == 2
    assert all(it["action"] == "network.create" for it in only_creates)
