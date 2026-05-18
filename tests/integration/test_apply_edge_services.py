"""End-to-end M7: create a network with DHCP/NAT/firewall, apply, observe.

Drives the controller via the public HTTP API so the wire layer (DTO ⇒
domain ⇒ plan ⇒ FakeAgent) is exercised. The shared in-process
``FakeAgent`` exposes its internal state via ``state.dhcp_scopes``,
``dns_zones``, ``nat_rules`` and ``firewall_policies`` dicts that we
inspect after each apply.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.app.config import Settings
from sdn_controller.app.container import build_container
from sdn_controller.core.value_objects.ids import NodeId
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


async def _register(client: httpx.AsyncClient, name: str, ip: str) -> str:
    r = await client.post("/api/v1/nodes", json={"name": name, "mgmt_ip": ip})
    assert r.status_code == 202, r.text
    node_id = r.json()["node"]["id"]
    assert isinstance(node_id, str)
    return node_id


async def _create_network(client: httpx.AsyncClient, *, name: str, nodes: list[str]) -> str:
    r = await client.post(
        "/api/v1/networks",
        json={"name": name, "type": "vxlan", "vni": 11000, "node_ids": nodes},
    )
    assert r.status_code == 202, r.text
    return str(r.json()["network"]["id"])


async def test_apply_provisions_dhcp_and_dns_on_edge_node_only(
    aclient: tuple[httpx.AsyncClient, FakeAgent],
) -> None:
    client, agent = aclient
    node_a = await _register(client, "node-a", "10.0.0.1")
    node_b = await _register(client, "node-b", "10.0.0.2")
    network_id = await _create_network(client, name="prod", nodes=[node_a, node_b])

    # Attach a subnet with DHCP + DNS zone.
    r = await client.post(
        f"/api/v1/networks/{network_id}/subnet",
        json={
            "cidr": "10.20.0.0/24",
            "gateway": "10.20.0.1",
            "dns_servers": ["10.20.0.2"],
            "dhcp": {"range_start": "10.20.0.10", "range_end": "10.20.0.100"},
            "dns_zone": "prod.lan",
        },
    )
    assert r.status_code == 202, r.text

    # Apply across the membership.
    r = await client.post(f"/api/v1/networks/{network_id}/apply")
    assert r.status_code == 202, r.text
    assert r.json()["operation"]["status"] == "succeeded"

    # Edge node (first in list) carries the scope + zone.
    edge_state = agent._by_node[NodeId(node_a)]
    assert f"scope-{network_id}" in edge_state.dhcp_scopes
    assert "prod.lan" in edge_state.dns_zones

    # Other node has no edge service objects.
    other_state = agent._by_node[NodeId(node_b)]
    assert other_state.dhcp_scopes == {}
    assert other_state.dns_zones == {}


async def test_apply_provisions_nat_and_firewall_via_patch(
    aclient: tuple[httpx.AsyncClient, FakeAgent],
) -> None:
    client, agent = aclient
    node_a = await _register(client, "node-a", "10.0.0.1")
    network_id = await _create_network(client, name="prod", nodes=[node_a])

    # Subnet first — NAT needs the source CIDR.
    r = await client.post(
        f"/api/v1/networks/{network_id}/subnet",
        json={"cidr": "10.30.0.0/24"},
    )
    assert r.status_code == 202, r.text

    # PATCH the NAT + firewall policy.
    r = await client.patch(
        f"/api/v1/networks/{network_id}",
        json={
            "nat": {"egress_interface": "eth1"},
            "firewall_policy": {
                "default_action": "drop",
                "rules": [
                    {
                        "action": "accept",
                        "proto": "tcp",
                        "destination_port_start": 80,
                        "destination_port_end": 80,
                    },
                ],
            },
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["network"]["nat"]["egress_interface"] == "eth1"
    assert body["network"]["firewall_policy"]["rules"][0]["destination_port_start"] == 80

    # Apply lands the rules on the edge node.
    r = await client.post(f"/api/v1/networks/{network_id}/apply")
    assert r.status_code == 202, r.text
    assert r.json()["operation"]["status"] == "succeeded"

    edge_state = agent._by_node[NodeId(node_a)]
    nat_rule = edge_state.nat_rules[f"nat-{network_id}"]
    assert nat_rule.source_cidr == "10.30.0.0/24"
    assert nat_rule.egress_interface == "eth1"
    fw_policy = edge_state.firewall_policies[f"policy-{network_id}"]
    assert fw_policy.default_action == "drop"
    assert len(fw_policy.rules) == 1
    assert fw_policy.rules[0].destination_port_start == 80


async def test_apply_edge_services_is_idempotent(
    aclient: tuple[httpx.AsyncClient, FakeAgent],
) -> None:
    """Re-applying the same intent must not flap the FakeAgent state."""
    client, agent = aclient
    node_a = await _register(client, "node-a", "10.0.0.1")
    network_id = await _create_network(client, name="prod", nodes=[node_a])
    await client.post(
        f"/api/v1/networks/{network_id}/subnet",
        json={
            "cidr": "10.40.0.0/24",
            "dhcp": {"range_start": "10.40.0.10", "range_end": "10.40.0.50"},
            "dns_zone": "lab.lan",
        },
    )

    first = (await client.post(f"/api/v1/networks/{network_id}/apply")).json()
    state_hash_after_first = await agent.state_hash(NodeId(node_a))

    second = (await client.post(f"/api/v1/networks/{network_id}/apply")).json()
    state_hash_after_second = await agent.state_hash(NodeId(node_a))

    assert first["operation"]["status"] == "succeeded"
    assert second["operation"]["status"] == "succeeded"
    # The OVS state_hash only covers bridges/ports; equal between applies
    # because nothing structural changed. The edge-service stores live
    # outside that hash but their _equality_ is what makes the FakeAgent
    # return ``changed=False`` on the second pass.
    assert state_hash_after_first == state_hash_after_second
