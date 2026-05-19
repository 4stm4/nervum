"""``/livez`` ↔ ``/readyz`` (SDN-039) и корреляция через
``X-Source-Task-Id``/``X-Operation-Id`` (SDN-056)."""

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
# /livez ↔ /readyz
# ---------------------------------------------------------------------------


async def test_livez_returns_200(aclient: httpx.AsyncClient) -> None:
    r = await aclient.get("/api/v1/livez")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}


async def test_readyz_returns_200_when_db_works(aclient: httpx.AsyncClient) -> None:
    r = await aclient.get("/api/v1/readyz")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}


async def test_health_alias_still_works(aclient: httpx.AsyncClient) -> None:
    """Старый ``/health`` оставлен для backwards-compat."""
    r = await aclient.get("/api/v1/health")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# X-Source-Task-Id / X-Operation-Id / actor
# ---------------------------------------------------------------------------


async def test_create_network_returns_operation_id_header(
    aclient: httpx.AsyncClient,
) -> None:
    r = await aclient.post(
        "/api/v1/networks",
        json={"name": "prod", "type": "flat"},
    )
    assert r.status_code == 202, r.text
    op_id = r.headers.get("x-operation-id")
    assert op_id
    # И он совпадает с тем, что в теле:
    assert op_id == r.json()["operation"]["operation_id"]


async def test_source_task_id_is_recorded_in_audit_actor(
    aclient: httpx.AsyncClient,
) -> None:
    r = await aclient.post(
        "/api/v1/networks",
        json={"name": "prod", "type": "flat"},
        headers={"x-source-task-id": "testum-task-42"},
    )
    assert r.status_code == 202, r.text

    events = (await aclient.get("/api/v1/audit-events")).json()["items"]
    create_event = next(it for it in events if it["action"] == "network.create")
    # auth_enabled=False → principal — заглушка ``auth-disabled``;
    # к ней приклеивается testum-task.
    assert "testum:testum-task-42" in (create_event["actor"] or "")
    assert create_event["payload"].get("source_task_id") == "testum-task-42"


async def test_create_network_records_created_by_with_source_task(
    aclient: httpx.AsyncClient,
) -> None:
    r = await aclient.post(
        "/api/v1/networks",
        json={"name": "ci-net", "type": "flat"},
        headers={"x-source-task-id": "task-99"},
    )
    op_id = r.json()["operation"]["operation_id"]
    op = (await aclient.get(f"/api/v1/operations/{op_id}")).json()
    assert "testum:task-99" in (op["created_by"] or "")


async def test_request_id_still_works(aclient: httpx.AsyncClient) -> None:
    """SDN-032 не сломался: X-Request-Id всё так же возвращается."""
    r = await aclient.get(
        "/api/v1/livez",
        headers={"x-request-id": "client-correlation-1"},
    )
    assert r.headers["x-request-id"] == "client-correlation-1"
