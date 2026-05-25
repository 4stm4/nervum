"""Integration tests for /api/v1/projects (N0 — multitenancy)."""

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
async def http(
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
    async with httpx.AsyncClient(transport=transport, base_url="http://controller") as client:
        yield client


@pytest.mark.anyio
async def test_list_projects_empty(http: httpx.AsyncClient) -> None:
    resp = await http.get("/api/v1/projects")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.anyio
async def test_create_and_get_project(http: httpx.AsyncClient) -> None:
    resp = await http.post(
        "/api/v1/projects",
        json={"name": "Alpha", "slug": "alpha", "labels": {"env": "staging"}},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "alpha"
    assert body["name"] == "Alpha"
    assert body["labels"] == {"env": "staging"}
    project_id = body["id"]

    get_resp = await http.get(f"/api/v1/projects/{project_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == project_id


@pytest.mark.anyio
async def test_duplicate_slug_returns_409(http: httpx.AsyncClient) -> None:
    await http.post("/api/v1/projects", json={"name": "A", "slug": "dupe"})
    resp = await http.post("/api/v1/projects", json={"name": "B", "slug": "dupe"})
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_update_project(http: httpx.AsyncClient) -> None:
    resp = await http.post("/api/v1/projects", json={"name": "Old", "slug": "upd"})
    project_id = resp.json()["id"]

    patch_resp = await http.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "Updated"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Updated"
    # slug unchanged
    assert patch_resp.json()["slug"] == "upd"


@pytest.mark.anyio
async def test_delete_project(http: httpx.AsyncClient) -> None:
    resp = await http.post("/api/v1/projects", json={"name": "Del", "slug": "del"})
    project_id = resp.json()["id"]

    del_resp = await http.delete(f"/api/v1/projects/{project_id}")
    assert del_resp.status_code == 204

    get_resp = await http.get(f"/api/v1/projects/{project_id}")
    assert get_resp.status_code == 404


@pytest.mark.anyio
async def test_get_missing_project_returns_404(http: httpx.AsyncClient) -> None:
    resp = await http.get("/api/v1/projects/proj_nope")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_project_member_crud(http: httpx.AsyncClient) -> None:
    # Create project
    proj_resp = await http.post(
        "/api/v1/projects", json={"name": "Membership", "slug": "membership"}
    )
    project_id = proj_resp.json()["id"]

    # Create a service account to add as member
    sa_resp = await http.post(
        "/api/v1/service-accounts",
        json={"name": "member-sa", "role": "viewer"},
    )
    assert sa_resp.status_code == 201, sa_resp.text
    sa_id = sa_resp.json()["id"]

    # Add member
    put_resp = await http.put(
        f"/api/v1/projects/{project_id}/members/{sa_id}",
        json={"service_account_id": sa_id, "role": "network_operator"},
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["role"] == "network_operator"

    # List members
    list_resp = await http.get(f"/api/v1/projects/{project_id}/members")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["items"]) == 1

    # Remove member
    del_resp = await http.delete(f"/api/v1/projects/{project_id}/members/{sa_id}")
    assert del_resp.status_code == 204

    list_resp2 = await http.get(f"/api/v1/projects/{project_id}/members")
    assert list_resp2.json()["items"] == []


@pytest.mark.anyio
async def test_schema_version_header(http: httpx.AsyncClient) -> None:
    """N0-05: Every API response carries X-SDN-Schema-Version: 2."""
    resp = await http.get("/api/v1/projects")
    assert resp.headers.get("x-sdn-schema-version") == "2"
