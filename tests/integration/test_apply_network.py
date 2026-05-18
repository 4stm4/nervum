"""ApplyNetwork end-to-end through the HTTP layer + FakeAgent.

Drives the M5 reconciler: registers nodes, creates a VXLAN network with
node membership, calls ``POST /networks/{id}/apply``, then verifies the
FakeAgent's state reflects the desired mesh. Also tests update + apply
re-converges, drift fixes, and operation status on failure.

We drive the controller asynchronously via ``httpx.AsyncClient`` +
``ASGITransport`` (same recipe as ``test_http_agent_client.py``) so we can
``await fake_agent.get_state(...)`` directly without spinning up a second
event loop.
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
from sdn_controller.core.services.diff_engine import (
    NETWORK_KEY,
    OWNER_KEY,
    OWNER_LABEL,
)
from sdn_controller.core.value_objects.ids import NodeId
from sdn_controller.ports.agent import DeleteBridgeStep, Plan
from tests.conftest import CountingIdFactory, FrozenClock, SequentialTokenFactory

# ---------------------------------------------------------------------------
# Async client + shared FakeAgent
# ---------------------------------------------------------------------------


@pytest.fixture
def shared_agent(clock: FrozenClock) -> FakeAgent:
    return FakeAgent(clock=clock)


@pytest.fixture
async def aclient(
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    shared_agent: FakeAgent,
) -> AsyncIterator[tuple[httpx.AsyncClient, FakeAgent]]:
    settings = Settings(persistence="memory", log_level="WARNING", log_format="console")
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
        yield http, shared_agent


async def _register_node(client: httpx.AsyncClient, name: str, ip: str) -> str:
    r = await client.post("/api/v1/nodes", json={"name": name, "mgmt_ip": ip})
    assert r.status_code == 202, r.text
    node_id = r.json()["node"]["id"]
    assert isinstance(node_id, str)
    return node_id


async def _create_vxlan_network(
    client: httpx.AsyncClient, *, name: str, vni: int, nodes: list[str]
) -> str:
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
# Create + spec_hash + intent_version
# ---------------------------------------------------------------------------


async def test_create_includes_node_ids_and_spec_hash(
    aclient: tuple[httpx.AsyncClient, FakeAgent],
) -> None:
    client, _ = aclient
    node_a = await _register_node(client, "node-a", "10.0.0.1")

    r = await client.post(
        "/api/v1/networks",
        json={
            "name": "prod",
            "type": "vxlan",
            "vni": 10100,
            "node_ids": [node_a],
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["network"]["node_ids"] == [node_a]
    assert body["network"]["intent_version"] == 1
    assert len(body["network"]["spec_hash"]) == 64


async def test_patch_bumps_intent_version_and_spec_hash(
    aclient: tuple[httpx.AsyncClient, FakeAgent],
) -> None:
    client, _ = aclient
    node_a = await _register_node(client, "node-a", "10.0.0.1")
    network_id = await _create_vxlan_network(client, name="prod", vni=10100, nodes=[node_a])
    original = (await client.get(f"/api/v1/networks/{network_id}")).json()

    r = await client.patch(f"/api/v1/networks/{network_id}", json={"mtu": 1450})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["network"]["intent_version"] == original["intent_version"] + 1
    assert body["network"]["spec_hash"] != original["spec_hash"]
    assert body["network"]["mtu"] == 1450


async def test_assign_nodes_replaces_membership(
    aclient: tuple[httpx.AsyncClient, FakeAgent],
) -> None:
    client, _ = aclient
    node_a = await _register_node(client, "node-a", "10.0.0.1")
    node_b = await _register_node(client, "node-b", "10.0.0.2")
    network_id = await _create_vxlan_network(client, name="prod", vni=10100, nodes=[node_a])

    r = await client.post(
        f"/api/v1/networks/{network_id}/nodes",
        json={"node_ids": [node_a, node_b]},
    )
    assert r.status_code == 202, r.text
    assert sorted(r.json()["network"]["node_ids"]) == sorted([node_a, node_b])


# ---------------------------------------------------------------------------
# Apply (reconcile)
# ---------------------------------------------------------------------------


async def test_apply_provisions_full_vxlan_mesh(
    aclient: tuple[httpx.AsyncClient, FakeAgent],
) -> None:
    client, agent = aclient
    node_a = await _register_node(client, "node-a", "10.0.0.1")
    node_b = await _register_node(client, "node-b", "10.0.0.2")
    node_c = await _register_node(client, "node-c", "10.0.0.3")
    network_id = await _create_vxlan_network(
        client, name="prod", vni=10100, nodes=[node_a, node_b, node_c]
    )

    body = await _post(client, f"/api/v1/networks/{network_id}/apply")
    assert body["operation"]["status"] == "succeeded"

    for node_id in (node_a, node_b, node_c):
        state = await agent.get_state(NodeId(node_id))
        bridge = state.find_bridge("br-prod")
        assert bridge is not None, f"node {node_id} missing bridge br-prod"
        assert bridge.external_ids[OWNER_KEY] == OWNER_LABEL
        assert bridge.external_ids[NETWORK_KEY] == network_id
        vxlans = [p for p in bridge.ports if p.interfaces and p.interfaces[0].type == "vxlan"]
        # Each node sees a tunnel to the other two.
        assert len(vxlans) == 2


async def test_apply_is_idempotent(aclient: tuple[httpx.AsyncClient, FakeAgent]) -> None:
    client, _ = aclient
    node_a = await _register_node(client, "node-a", "10.0.0.1")
    node_b = await _register_node(client, "node-b", "10.0.0.2")
    network_id = await _create_vxlan_network(client, name="prod", vni=10100, nodes=[node_a, node_b])

    first = await _post(client, f"/api/v1/networks/{network_id}/apply")
    second = await _post(client, f"/api/v1/networks/{network_id}/apply")

    assert first["operation"]["status"] == "succeeded"
    assert second["operation"]["status"] == "succeeded"


async def test_apply_for_network_with_no_nodes_succeeds_trivially(
    aclient: tuple[httpx.AsyncClient, FakeAgent],
) -> None:
    client, _ = aclient
    network_id = await _create_vxlan_network(client, name="orphan", vni=10101, nodes=[])

    body = await _post(client, f"/api/v1/networks/{network_id}/apply")
    assert body["operation"]["status"] == "succeeded"


async def test_apply_with_missing_node_fails_operation(
    aclient: tuple[httpx.AsyncClient, FakeAgent],
) -> None:
    client, _ = aclient
    node_a = await _register_node(client, "node-a", "10.0.0.1")
    network_id = await _create_vxlan_network(client, name="prod", vni=10100, nodes=[node_a])
    await client.delete(f"/api/v1/nodes/{node_a}")

    body = await _post(client, f"/api/v1/networks/{network_id}/apply")
    assert body["operation"]["status"] == "failed"


async def test_apply_reconciles_drift(aclient: tuple[httpx.AsyncClient, FakeAgent]) -> None:
    """Mutate agent state out-of-band, then apply — diff engine restores it."""
    client, agent = aclient
    node_a = await _register_node(client, "node-a", "10.0.0.1")
    node_b = await _register_node(client, "node-b", "10.0.0.2")
    network_id = await _create_vxlan_network(client, name="prod", vni=10100, nodes=[node_a, node_b])
    await _post(client, f"/api/v1/networks/{network_id}/apply")

    await agent.apply_plan(
        NodeId(node_a),
        Plan(plan_id="drift", steps=(DeleteBridgeStep(name="br-prod"),)),
    )

    body = await _post(client, f"/api/v1/networks/{network_id}/apply")
    assert body["operation"]["status"] == "succeeded"

    state = await agent.get_state(NodeId(node_a))
    assert state.find_bridge("br-prod") is not None
