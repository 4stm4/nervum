"""Integration-тесты ``GET /topology`` и ``GET /drift``.

Прогоняем полный путь HTTP → use case → in-memory репозитории.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

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


async def _register(client: httpx.AsyncClient, name: str, ip: str) -> str:
    r = await client.post("/api/v1/nodes", json={"name": name, "mgmt_ip": ip})
    assert r.status_code == 202, r.text
    node_id = r.json()["node"]["id"]
    assert isinstance(node_id, str)
    return node_id


async def _create_vxlan(client: httpx.AsyncClient, *, name: str, vni: int, nodes: list[str]) -> str:
    r = await client.post(
        "/api/v1/networks",
        json={"name": name, "type": "vxlan", "vni": vni, "node_ids": nodes},
    )
    assert r.status_code == 202, r.text
    return str(r.json()["network"]["id"])


async def _post(client: httpx.AsyncClient, path: str, **kwargs: Any) -> dict[str, Any]:
    r = await client.post(path, **kwargs)
    assert r.status_code == 202, r.text
    body: dict[str, Any] = r.json()
    return body


# ---------------------------------------------------------------------------
# /topology
# ---------------------------------------------------------------------------


async def test_topology_empty_state(aclient: httpx.AsyncClient) -> None:
    r = await aclient.get("/api/v1/topology")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["nodes"] == []
    assert body["networks"] == []
    assert body["bridges"] == []
    assert body["edges"] == []


async def test_topology_after_apply_includes_bridges_and_edges(
    aclient: httpx.AsyncClient,
) -> None:
    node_a = await _register(aclient, "node-a", "10.0.0.1")
    node_b = await _register(aclient, "node-b", "10.0.0.2")
    network_id = await _create_vxlan(aclient, name="prod", vni=10100, nodes=[node_a, node_b])
    apply_body = await _post(aclient, f"/api/v1/networks/{network_id}/apply")
    assert apply_body["operation"]["status"] == "succeeded"

    r = await aclient.get("/api/v1/topology")
    assert r.status_code == 200, r.text
    body = r.json()

    # Каждый узел отчитывается observed state после apply.
    assert {n["id"] for n in body["nodes"]} == {node_a, node_b}
    for n in body["nodes"]:
        assert n["observed_state_hash"] is not None
        assert n["observed_at"] is not None

    # Сеть в графе с правильными vni и member-узлами.
    assert len(body["networks"]) == 1
    assert sorted(body["networks"][0]["node_ids"]) == sorted([node_a, node_b])
    assert body["networks"][0]["vni"] == 10100

    # Мосты на каждом узле, привязка к сети через network_id.
    bridges_by_node = {b["node_id"]: b for b in body["bridges"]}
    assert set(bridges_by_node) == {node_a, node_b}
    for b in bridges_by_node.values():
        assert b["name"] == "br-prod"
        assert b["network_id"] == network_id

    # Рёбра: 2 node_network + 1 vxlan_tunnel.
    kinds = sorted(e["kind"] for e in body["edges"])
    assert kinds == ["node_network", "node_network", "vxlan_tunnel"]


# ---------------------------------------------------------------------------
# /drift
# ---------------------------------------------------------------------------


async def test_drift_empty_when_nothing_to_compare(aclient: httpx.AsyncClient) -> None:
    r = await aclient.get("/api/v1/drift")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["stale_nodes"] == []


async def test_drift_clean_after_apply(aclient: httpx.AsyncClient) -> None:
    node_a = await _register(aclient, "node-a", "10.0.0.1")
    node_b = await _register(aclient, "node-b", "10.0.0.2")
    network_id = await _create_vxlan(aclient, name="prod", vni=10100, nodes=[node_a, node_b])
    await _post(aclient, f"/api/v1/networks/{network_id}/apply")

    r = await aclient.get("/api/v1/drift")
    assert r.status_code == 200, r.text
    body = r.json()
    # Apply прошёл и observed state обновился — дрейфа нет.
    assert body["items"] == []
    assert body["stale_nodes"] == []


async def test_drift_surfaces_node_without_observed_state(aclient: httpx.AsyncClient) -> None:
    node_a = await _register(aclient, "node-a", "10.0.0.1")
    network_id = await _create_vxlan(aclient, name="prod", vni=10100, nodes=[node_a])
    # Нет apply → observed state нигде не сохранён.

    r = await aclient.get("/api/v1/drift")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale_nodes"] == [node_a]
    assert body["items"] == []
    assert network_id in {n["id"] for n in (await aclient.get("/api/v1/networks")).json()["items"]}
