"""Integration tests for N1 HTTP endpoints.

Covers: /api/v1/logical-ports, /api/v1/security-groups,
        /api/v1/address-pools, /api/v1/service-objects,
        /api/v1/qos-policies, /api/v1/nodes/{id}/maintenance
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


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register_node(http: httpx.AsyncClient) -> str:
    """Register a node and return its ID."""
    resp = await http.post(
        "/api/v1/nodes",
        json={"name": "edge-1", "mgmt_ip": "10.0.0.1", "roles": [], "labels": {}},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["node"]["id"]


async def _create_network(http: httpx.AsyncClient) -> str:
    """Create a network and return its ID."""
    resp = await http.post(
        "/api/v1/networks",
        json={"name": "default", "type": "vxlan", "vni": 100, "labels": {}},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["network"]["id"]


# ---------------------------------------------------------------------------
# LogicalPort
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_logical_port_list_empty(http: httpx.AsyncClient) -> None:
    resp = await http.get("/api/v1/logical-ports")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.anyio
async def test_logical_port_crud(http: httpx.AsyncClient) -> None:
    node_id = await _register_node(http)
    net_id = await _create_network(http)

    # Create
    resp = await http.post(
        "/api/v1/logical-ports",
        json={
            "name": "eth0",
            "node_id": node_id,
            "network_id": net_id,
            "mac_address": "02:aa:bb:cc:dd:ee",
            "labels": {"env": "test"},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    port_id = body["id"]
    assert body["name"] == "eth0"
    assert body["status"] == "pending"
    assert body["mac_address"] == "02:aa:bb:cc:dd:ee"

    # Get
    get_resp = await http.get(f"/api/v1/logical-ports/{port_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == port_id

    # Update
    patch_resp = await http.patch(
        f"/api/v1/logical-ports/{port_id}",
        json={"name": "eth0-renamed"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "eth0-renamed"

    # List (should contain our port)
    list_resp = await http.get("/api/v1/logical-ports")
    assert list_resp.status_code == 200
    ids = [p["id"] for p in list_resp.json()["items"]]
    assert port_id in ids


@pytest.mark.anyio
async def test_logical_port_attach_detach(http: httpx.AsyncClient) -> None:
    node_id = await _register_node(http)
    net_id = await _create_network(http)

    create_resp = await http.post(
        "/api/v1/logical-ports",
        json={"name": "eth0", "node_id": node_id, "network_id": net_id},
    )
    assert create_resp.status_code == 201, create_resp.text
    port_id = create_resp.json()["id"]

    # Attach
    attach_resp = await http.post(
        f"/api/v1/logical-ports/{port_id}/attach",
        json={"vif_id": "vif-42"},
    )
    assert attach_resp.status_code == 200
    assert attach_resp.json()["status"] == "active"
    assert attach_resp.json()["vif_id"] == "vif-42"

    # Detach
    detach_resp = await http.post(f"/api/v1/logical-ports/{port_id}/detach", json={})
    assert detach_resp.status_code == 200
    assert detach_resp.json()["status"] == "detached"


@pytest.mark.anyio
async def test_logical_port_delete(http: httpx.AsyncClient) -> None:
    node_id = await _register_node(http)
    net_id = await _create_network(http)

    create_resp = await http.post(
        "/api/v1/logical-ports",
        json={"name": "eth0", "node_id": node_id, "network_id": net_id},
    )
    port_id = create_resp.json()["id"]

    del_resp = await http.delete(f"/api/v1/logical-ports/{port_id}")
    assert del_resp.status_code == 204

    get_resp = await http.get(f"/api/v1/logical-ports/{port_id}")
    assert get_resp.status_code == 404


@pytest.mark.anyio
async def test_logical_port_unknown_node_returns_404(http: httpx.AsyncClient) -> None:
    net_id = await _create_network(http)
    resp = await http.post(
        "/api/v1/logical-ports",
        json={"name": "eth0", "node_id": "ghost-node", "network_id": net_id},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_logical_port_filter_by_node(http: httpx.AsyncClient) -> None:
    node_id = await _register_node(http)
    net_id = await _create_network(http)

    await http.post(
        "/api/v1/logical-ports",
        json={"name": "p1", "node_id": node_id, "network_id": net_id},
    )

    list_resp = await http.get(f"/api/v1/logical-ports?node_id={node_id}")
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) >= 1
    assert all(p["node_id"] == node_id for p in items)


# ---------------------------------------------------------------------------
# SecurityGroup
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_security_group_list_empty(http: httpx.AsyncClient) -> None:
    resp = await http.get("/api/v1/security-groups")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.anyio
async def test_security_group_crud(http: httpx.AsyncClient) -> None:
    # Create
    resp = await http.post(
        "/api/v1/security-groups",
        json={"name": "web-sg", "description": "web tier", "labels": {"tier": "web"}},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    sg_id = body["id"]
    assert body["name"] == "web-sg"
    assert body["description"] == "web tier"

    # Get
    get_resp = await http.get(f"/api/v1/security-groups/{sg_id}")
    assert get_resp.status_code == 200

    # Update
    patch_resp = await http.patch(
        f"/api/v1/security-groups/{sg_id}",
        json={"name": "web-sg-v2"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "web-sg-v2"

    # Delete
    del_resp = await http.delete(f"/api/v1/security-groups/{sg_id}")
    assert del_resp.status_code == 204

    get_after = await http.get(f"/api/v1/security-groups/{sg_id}")
    assert get_after.status_code == 404


@pytest.mark.anyio
async def test_security_group_members(http: httpx.AsyncClient) -> None:
    # Create SG
    sg_resp = await http.post(
        "/api/v1/security-groups",
        json={"name": "sg-members-test"},
    )
    sg_id = sg_resp.json()["id"]

    # List empty
    list_resp = await http.get(f"/api/v1/security-groups/{sg_id}/members")
    assert list_resp.status_code == 200
    assert list_resp.json()["items"] == []

    # Add member (use logical_port type to avoid URL-encoding issues with CIDR slashes)
    add_resp = await http.post(
        f"/api/v1/security-groups/{sg_id}/members",
        json={"member_type": "logical_port", "member_value": "lport_42"},
    )
    assert add_resp.status_code == 201, add_resp.text

    # List has one member
    list2 = await http.get(f"/api/v1/security-groups/{sg_id}/members")
    assert len(list2.json()["items"]) == 1
    m = list2.json()["items"][0]
    assert m["member_type"] == "logical_port"
    assert m["member_value"] == "lport_42"

    # Remove member
    del_m = await http.delete(
        f"/api/v1/security-groups/{sg_id}/members/logical_port/lport_42"
    )
    assert del_m.status_code == 204

    list3 = await http.get(f"/api/v1/security-groups/{sg_id}/members")
    assert list3.json()["items"] == []


# ---------------------------------------------------------------------------
# AddressPool
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_address_pool_crud(http: httpx.AsyncClient) -> None:
    # Create
    resp = await http.post(
        "/api/v1/address-pools",
        json={"name": "prod-pool", "cidrs": ["10.0.0.0/8", "192.168.0.0/16"]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    pool_id = body["id"]
    assert body["name"] == "prod-pool"
    assert "10.0.0.0/8" in body["cidrs"]

    # Get
    get_resp = await http.get(f"/api/v1/address-pools/{pool_id}")
    assert get_resp.status_code == 200

    # Update
    patch_resp = await http.patch(
        f"/api/v1/address-pools/{pool_id}",
        json={"cidrs": ["172.16.0.0/12"]},
    )
    assert patch_resp.status_code == 200
    assert "172.16.0.0/12" in patch_resp.json()["cidrs"]

    # List
    list_resp = await http.get("/api/v1/address-pools")
    assert list_resp.status_code == 200
    assert any(p["id"] == pool_id for p in list_resp.json()["items"])

    # Delete
    del_resp = await http.delete(f"/api/v1/address-pools/{pool_id}")
    assert del_resp.status_code == 204

    get_after = await http.get(f"/api/v1/address-pools/{pool_id}")
    assert get_after.status_code == 404


@pytest.mark.anyio
async def test_address_pool_invalid_cidr_returns_422(http: httpx.AsyncClient) -> None:
    resp = await http.post(
        "/api/v1/address-pools",
        json={"name": "bad", "cidrs": ["not-a-cidr"]},
    )
    assert resp.status_code in (400, 422), resp.text


# ---------------------------------------------------------------------------
# ServiceObject
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_service_object_crud(http: httpx.AsyncClient) -> None:
    # Create
    resp = await http.post(
        "/api/v1/service-objects",
        json={"name": "http", "protocol": "tcp", "ports": ["80", "443"]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    obj_id = body["id"]
    assert body["protocol"] == "tcp"
    assert "80" in body["ports"]

    # Get
    get_resp = await http.get(f"/api/v1/service-objects/{obj_id}")
    assert get_resp.status_code == 200

    # Update
    patch_resp = await http.patch(
        f"/api/v1/service-objects/{obj_id}",
        json={"ports": ["8080", "8443"]},
    )
    assert patch_resp.status_code == 200
    assert "8080" in patch_resp.json()["ports"]

    # Delete
    del_resp = await http.delete(f"/api/v1/service-objects/{obj_id}")
    assert del_resp.status_code == 204


@pytest.mark.anyio
async def test_service_object_icmp_no_ports(http: httpx.AsyncClient) -> None:
    resp = await http.post(
        "/api/v1/service-objects",
        json={"name": "ping", "protocol": "icmp"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["ports"] == []


@pytest.mark.anyio
async def test_service_object_icmp_with_ports_rejected(http: httpx.AsyncClient) -> None:
    resp = await http.post(
        "/api/v1/service-objects",
        json={"name": "bad", "protocol": "icmp", "ports": ["80"]},
    )
    assert resp.status_code in (400, 422), resp.text


# ---------------------------------------------------------------------------
# QosPolicy
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_qos_policy_crud(http: httpx.AsyncClient) -> None:
    # Create
    resp = await http.post(
        "/api/v1/qos-policies",
        json={"name": "gold", "ingress_kbps": 10000, "egress_kbps": 5000, "dscp": 46},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    policy_id = body["id"]
    assert body["ingress_kbps"] == 10000
    assert body["dscp"] == 46

    # Get
    get_resp = await http.get(f"/api/v1/qos-policies/{policy_id}")
    assert get_resp.status_code == 200

    # Update
    patch_resp = await http.patch(
        f"/api/v1/qos-policies/{policy_id}",
        json={"egress_kbps": 8000},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["egress_kbps"] == 8000

    # List
    list_resp = await http.get("/api/v1/qos-policies")
    assert list_resp.status_code == 200
    assert any(p["id"] == policy_id for p in list_resp.json()["items"])

    # Delete
    del_resp = await http.delete(f"/api/v1/qos-policies/{policy_id}")
    assert del_resp.status_code == 204

    get_after = await http.get(f"/api/v1/qos-policies/{policy_id}")
    assert get_after.status_code == 404


@pytest.mark.anyio
async def test_qos_policy_invalid_dscp_returns_error(http: httpx.AsyncClient) -> None:
    resp = await http.post(
        "/api/v1/qos-policies",
        json={"name": "bad", "dscp": 99},
    )
    assert resp.status_code in (400, 422), resp.text


# ---------------------------------------------------------------------------
# Node maintenance mode
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_node_maintenance_enter_exit(http: httpx.AsyncClient) -> None:
    node_id = await _register_node(http)

    # Enter maintenance
    enter_resp = await http.post(f"/api/v1/nodes/{node_id}/maintenance")
    assert enter_resp.status_code == 204, enter_resp.text

    # Exit maintenance
    exit_resp = await http.delete(f"/api/v1/nodes/{node_id}/maintenance")
    assert exit_resp.status_code == 204, exit_resp.text


@pytest.mark.anyio
async def test_node_maintenance_unknown_node_returns_404(
    http: httpx.AsyncClient,
) -> None:
    resp = await http.post("/api/v1/nodes/ghost-node/maintenance")
    assert resp.status_code == 404
