"""Integration-тесты HTTP с включённым auth (M9 — SDN-028/030).

Гоняем полный путь: bootstrap admin token → создать service account →
выпустить токен → попробовать всё разрешённое и запретное под viewer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.app.config import Settings
from sdn_controller.app.container import build_container
from tests.conftest import CountingIdFactory, FrozenClock, SequentialTokenFactory

_BOOTSTRAP_TOKEN = "bootstrap-secret"


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
        auth_enabled=True,
        auth_bootstrap_admin_token=_BOOTSTRAP_TOKEN,
    )
    container = build_container(
        settings,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        agent=shared_agent,
    )
    # ASGITransport не гоняет lifespan-events по умолчанию, поэтому
    # bootstrap() вызываем явно — иначе bootstrap-токена нет в БД.
    await container.bootstrap()
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://controller") as http:
        yield http


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Bootstrap admin token
# ---------------------------------------------------------------------------


async def test_bootstrap_admin_token_works_from_start(aclient: httpx.AsyncClient) -> None:
    r = await aclient.get("/api/v1/networks", headers=_auth(_BOOTSTRAP_TOKEN))
    assert r.status_code == 200, r.text


async def test_request_without_auth_returns_401(aclient: httpx.AsyncClient) -> None:
    r = await aclient.get("/api/v1/networks")
    assert r.status_code == 401, r.text
    assert r.json()["error"]["code"] == "unauthorized"


async def test_bogus_bearer_token_returns_401(aclient: httpx.AsyncClient) -> None:
    r = await aclient.get("/api/v1/networks", headers=_auth("not-a-real-token"))
    assert r.status_code == 401, r.text


async def test_malformed_authorization_header_returns_401(aclient: httpx.AsyncClient) -> None:
    r = await aclient.get("/api/v1/networks", headers={"Authorization": "Token abc"})
    assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# Service accounts: создать → выпустить токен → использовать
# ---------------------------------------------------------------------------


async def test_admin_can_create_account_and_issue_token(aclient: httpx.AsyncClient) -> None:
    admin_h = _auth(_BOOTSTRAP_TOKEN)

    create = await aclient.post(
        "/api/v1/service-accounts",
        json={"name": "viewer-ci", "role": "viewer"},
        headers=admin_h,
    )
    assert create.status_code == 201, create.text
    account = create.json()
    assert account["role"] == "viewer"

    issued = await aclient.post(
        f"/api/v1/service-accounts/{account['id']}/tokens",
        json={"ttl_seconds": 3600},
        headers=admin_h,
    )
    assert issued.status_code == 201, issued.text
    plaintext = issued.json()["plaintext"]
    assert plaintext

    # Этот токен теперь работает для viewer-ограниченного чтения.
    list_nets = await aclient.get("/api/v1/networks", headers=_auth(plaintext))
    assert list_nets.status_code == 200, list_nets.text


# ---------------------------------------------------------------------------
# RBAC: viewer не может писать, network_operator — может
# ---------------------------------------------------------------------------


async def _issue_token_for_role(aclient: httpx.AsyncClient, *, name: str, role: str) -> str:
    admin_h = _auth(_BOOTSTRAP_TOKEN)
    sa = (
        await aclient.post(
            "/api/v1/service-accounts",
            json={"name": name, "role": role},
            headers=admin_h,
        )
    ).json()
    plaintext = (
        await aclient.post(
            f"/api/v1/service-accounts/{sa['id']}/tokens",
            json={},
            headers=admin_h,
        )
    ).json()["plaintext"]
    return str(plaintext)


async def test_viewer_can_read_but_not_write(aclient: httpx.AsyncClient) -> None:
    token = await _issue_token_for_role(aclient, name="vi", role="viewer")
    h = _auth(token)

    assert (await aclient.get("/api/v1/networks", headers=h)).status_code == 200
    forbidden = await aclient.post(
        "/api/v1/networks",
        json={"name": "x", "type": "flat"},
        headers=h,
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["error"]["code"] == "forbidden"


async def test_viewer_cannot_see_service_accounts(aclient: httpx.AsyncClient) -> None:
    token = await _issue_token_for_role(aclient, name="vi-2", role="viewer")
    r = await aclient.get("/api/v1/service-accounts", headers=_auth(token))
    assert r.status_code == 403, r.text


async def test_network_operator_can_create_network(aclient: httpx.AsyncClient) -> None:
    token = await _issue_token_for_role(aclient, name="net-op", role="network_operator")
    r = await aclient.post(
        "/api/v1/networks",
        json={"name": "ops-net", "type": "flat"},
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text


async def test_network_operator_cannot_admin_security(aclient: httpx.AsyncClient) -> None:
    token = await _issue_token_for_role(aclient, name="net-op-2", role="network_operator")
    r = await aclient.post(
        "/api/v1/service-accounts",
        json={"name": "rogue", "role": "admin"},
        headers=_auth(token),
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


async def test_revoked_token_no_longer_works(aclient: httpx.AsyncClient) -> None:
    admin_h = _auth(_BOOTSTRAP_TOKEN)
    sa = (
        await aclient.post(
            "/api/v1/service-accounts",
            json={"name": "ops", "role": "viewer"},
            headers=admin_h,
        )
    ).json()
    issued = (
        await aclient.post(
            f"/api/v1/service-accounts/{sa['id']}/tokens",
            json={},
            headers=admin_h,
        )
    ).json()
    plaintext = issued["plaintext"]

    # До отзыва — работает.
    assert (await aclient.get("/api/v1/networks", headers=_auth(plaintext))).status_code == 200

    revoke = await aclient.post(
        f"/api/v1/service-tokens/{issued['token']['id']}/revoke",
        headers=admin_h,
    )
    assert revoke.status_code == 200, revoke.text

    # После отзыва — 401.
    r = await aclient.get("/api/v1/networks", headers=_auth(plaintext))
    assert r.status_code == 401, r.text


async def test_disabled_account_revokes_all_its_tokens(aclient: httpx.AsyncClient) -> None:
    admin_h = _auth(_BOOTSTRAP_TOKEN)
    sa = (
        await aclient.post(
            "/api/v1/service-accounts",
            json={"name": "tmp", "role": "viewer"},
            headers=admin_h,
        )
    ).json()
    plaintext = (
        await aclient.post(
            f"/api/v1/service-accounts/{sa['id']}/tokens",
            json={},
            headers=admin_h,
        )
    ).json()["plaintext"]

    disable = await aclient.post(
        f"/api/v1/service-accounts/{sa['id']}/disable",
        headers=admin_h,
    )
    assert disable.status_code == 200, disable.text

    r = await aclient.get("/api/v1/networks", headers=_auth(plaintext))
    assert r.status_code == 401, r.text
