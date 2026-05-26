"""HTTP integration-тесты N3 — Router, FloatingIP, BgpPeer.

Используют in-memory адаптеры. Каждый тест независим: контейнер
пересоздаётся через фикстуры из conftest.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _create_router(client: TestClient, **kwargs: object) -> dict:
    payload = {"name": "gw", **kwargs}
    resp = client.post("/api/v1/routers", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["router"]


def _create_router_with_ext(client: TestClient) -> dict:
    return _create_router(client, external_network_id="net_ext_1")


# ---------------------------------------------------------------------------
# Router CRUD
# ---------------------------------------------------------------------------


class TestRouterCRUD:
    def test_create_router(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/routers",
            json={"name": "gw", "description": "main gateway"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "router" in body
        router = body["router"]
        assert router["name"] == "gw"
        assert router["status"] == "build"
        assert router["admin_state_up"] is True
        assert router["ha_mode"] == "none"
        assert router["id"].startswith("rtr_")

    def test_list_routers_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/routers")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_list_routers(self, client: TestClient) -> None:
        _create_router(client, name="gw1")
        _create_router(client, name="gw2")
        resp = client.get("/api/v1/routers")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 2

    def test_get_router(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.get(f"/api/v1/routers/{router['id']}")
        assert resp.status_code == 200
        assert resp.json()["router"]["id"] == router["id"]

    def test_get_router_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/routers/rtr_999")
        assert resp.status_code == 404

    def test_update_router(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.patch(
            f"/api/v1/routers/{router['id']}",
            json={"name": "renamed"},
        )
        assert resp.status_code == 200
        assert resp.json()["router"]["name"] == "renamed"

    def test_delete_router(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.delete(f"/api/v1/routers/{router['id']}")
        assert resp.status_code == 204
        resp2 = client.get(f"/api/v1/routers/{router['id']}")
        assert resp2.status_code == 404


class TestRouterHA:
    def test_create_vrrp_router(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/routers",
            json={"name": "ha-gw", "ha_mode": "vrrp", "vrrp_priority": 100, "vrrp_vrid": 10},
        )
        assert resp.status_code == 201, resp.text
        router = resp.json()["router"]
        assert router["ha_mode"] == "vrrp"
        assert router["vrrp_priority"] == 100
        assert router["vrrp_vrid"] == 10


class TestRouterAdminState:
    def test_disable_router(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.put(
            f"/api/v1/routers/{router['id']}/admin-state",
            json={"admin_state_up": False},
        )
        assert resp.status_code == 200
        assert resp.json()["router"]["admin_state_up"] is False
        assert resp.json()["router"]["status"] == "down"

    def test_enable_router(self, client: TestClient) -> None:
        router = _create_router(client)
        client.put(
            f"/api/v1/routers/{router['id']}/admin-state",
            json={"admin_state_up": False},
        )
        resp = client.put(
            f"/api/v1/routers/{router['id']}/admin-state",
            json={"admin_state_up": True},
        )
        assert resp.status_code == 200
        assert resp.json()["router"]["admin_state_up"] is True


# ---------------------------------------------------------------------------
# Статические маршруты
# ---------------------------------------------------------------------------


class TestStaticRoutesHTTP:
    def test_add_route(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.post(
            f"/api/v1/routers/{router['id']}/routes",
            json={"destination": "10.0.0.0/8", "nexthop": "192.168.1.1"},
        )
        assert resp.status_code == 201, resp.text
        routes = resp.json()["router"]["static_routes"]
        assert len(routes) == 1
        assert routes[0]["destination"] == "10.0.0.0/8"

    def test_remove_route(self, client: TestClient) -> None:
        router = _create_router(client)
        client.post(
            f"/api/v1/routers/{router['id']}/routes",
            json={"destination": "10.0.0.0/8", "nexthop": "192.168.1.1"},
        )
        resp = client.delete(f"/api/v1/routers/{router['id']}/routes/10.0.0.0/8")
        assert resp.status_code == 204

    def test_add_duplicate_route_fails(self, client: TestClient) -> None:
        router = _create_router(client)
        client.post(
            f"/api/v1/routers/{router['id']}/routes",
            json={"destination": "10.0.0.0/8", "nexthop": "192.168.1.1"},
        )
        resp = client.post(
            f"/api/v1/routers/{router['id']}/routes",
            json={"destination": "10.0.0.0/8", "nexthop": "192.168.1.2"},
        )
        assert resp.status_code in (400, 422), resp.text


# ---------------------------------------------------------------------------
# Внутренние сети
# ---------------------------------------------------------------------------


class TestInternalNetworksHTTP:
    def test_add_network(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.post(
            f"/api/v1/routers/{router['id']}/networks",
            json={"network_id": "net_1"},
        )
        assert resp.status_code == 201, resp.text
        nets = resp.json()["router"]["internal_network_ids"]
        assert "net_1" in nets

    def test_remove_network(self, client: TestClient) -> None:
        router = _create_router(client)
        client.post(
            f"/api/v1/routers/{router['id']}/networks",
            json={"network_id": "net_1"},
        )
        resp = client.delete(f"/api/v1/routers/{router['id']}/networks/net_1")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Apply router (N3-03)
# ---------------------------------------------------------------------------


class TestApplyRouterHTTP:
    def test_apply_sets_active(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.post(f"/api/v1/routers/{router['id']}/apply")
        assert resp.status_code == 200, resp.text
        data = resp.json()["router"]
        assert data["status"] == "active"
        assert data["applied_config"] is not None
        assert data["applied_at"] is not None

    def test_apply_with_route_contains_ip_route(self, client: TestClient) -> None:
        router = _create_router(client)
        client.post(
            f"/api/v1/routers/{router['id']}/routes",
            json={"destination": "0.0.0.0/0", "nexthop": "10.0.0.1"},
        )
        resp = client.post(f"/api/v1/routers/{router['id']}/apply")
        assert resp.status_code == 200, resp.text
        cfg = resp.json()["router"]["applied_config"]
        assert "ip route replace 0.0.0.0/0 via 10.0.0.1" in cfg


# ---------------------------------------------------------------------------
# FloatingIP CRUD (N3-02)
# ---------------------------------------------------------------------------


class TestFloatingIpHTTP:
    def test_allocate_fip(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/floating-ips",
            json={
                "external_network_id": "net_ext",
                "floating_ip_address": "1.2.3.4",
            },
        )
        assert resp.status_code == 201, resp.text
        fip = resp.json()["floating_ip"]
        assert fip["floating_ip_address"] == "1.2.3.4"
        assert fip["status"] == "down"
        assert fip["id"].startswith("fip_")

    def test_list_fips_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/floating-ips")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_get_fip(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/floating-ips",
            json={"external_network_id": "net_ext", "floating_ip_address": "1.2.3.4"},
        )
        fip_id = resp.json()["floating_ip"]["id"]
        resp2 = client.get(f"/api/v1/floating-ips/{fip_id}")
        assert resp2.status_code == 200
        assert resp2.json()["floating_ip"]["id"] == fip_id

    def test_release_fip(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/floating-ips",
            json={"external_network_id": "net_ext", "floating_ip_address": "1.2.3.4"},
        )
        fip_id = resp.json()["floating_ip"]["id"]
        resp2 = client.delete(f"/api/v1/floating-ips/{fip_id}")
        assert resp2.status_code == 204
        resp3 = client.get(f"/api/v1/floating-ips/{fip_id}")
        assert resp3.status_code == 404

    def test_associate_and_disassociate(self, client: TestClient) -> None:
        # создаём маршрутизатор
        router = _create_router(client)
        # выделяем FIP
        resp = client.post(
            "/api/v1/floating-ips",
            json={"external_network_id": "net_ext", "floating_ip_address": "1.2.3.4"},
        )
        fip_id = resp.json()["floating_ip"]["id"]
        # ассоциируем
        resp2 = client.post(
            f"/api/v1/floating-ips/{fip_id}/associate",
            json={
                "logical_port_id": "lport_1",
                "fixed_ip_address": "192.168.1.10",
                "router_id": router["id"],
            },
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["floating_ip"]["status"] == "active"
        # снимаем ассоциацию
        resp3 = client.post(f"/api/v1/floating-ips/{fip_id}/disassociate")
        assert resp3.status_code == 200
        assert resp3.json()["floating_ip"]["status"] == "down"

    def test_disassociate_non_active_fails(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/floating-ips",
            json={"external_network_id": "net_ext", "floating_ip_address": "1.2.3.4"},
        )
        fip_id = resp.json()["floating_ip"]["id"]
        resp2 = client.post(f"/api/v1/floating-ips/{fip_id}/disassociate")
        assert resp2.status_code in (400, 422)


# ---------------------------------------------------------------------------
# BgpPeer CRUD (N3-05)
# ---------------------------------------------------------------------------


class TestBgpPeerHTTP:
    def test_create_bgp_peer(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.post(
            "/api/v1/bgp-peers",
            json={
                "router_id": router["id"],
                "peer_ip": "192.168.1.2",
                "peer_asn": 65001,
                "local_asn": 65000,
            },
        )
        assert resp.status_code == 201, resp.text
        peer = resp.json()["bgp_peer"]
        assert peer["peer_ip"] == "192.168.1.2"
        assert peer["state"] == "idle"
        assert peer["id"].startswith("bgpp_")

    def test_list_bgp_peers_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/bgp-peers")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_list_bgp_peers_by_router(self, client: TestClient) -> None:
        r1 = _create_router(client, name="gw1")
        r2 = _create_router(client, name="gw2")
        client.post(
            "/api/v1/bgp-peers",
            json={"router_id": r1["id"], "peer_ip": "1.1.1.1", "peer_asn": 65001, "local_asn": 65000},
        )
        client.post(
            "/api/v1/bgp-peers",
            json={"router_id": r2["id"], "peer_ip": "2.2.2.2", "peer_asn": 65002, "local_asn": 65000},
        )
        resp = client.get(f"/api/v1/bgp-peers?router_id={r1['id']}")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["peer_ip"] == "1.1.1.1"

    def test_get_bgp_peer(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.post(
            "/api/v1/bgp-peers",
            json={"router_id": router["id"], "peer_ip": "1.1.1.1", "peer_asn": 65001, "local_asn": 65000},
        )
        peer_id = resp.json()["bgp_peer"]["id"]
        resp2 = client.get(f"/api/v1/bgp-peers/{peer_id}")
        assert resp2.status_code == 200
        assert resp2.json()["bgp_peer"]["id"] == peer_id

    def test_delete_bgp_peer(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.post(
            "/api/v1/bgp-peers",
            json={"router_id": router["id"], "peer_ip": "1.1.1.1", "peer_asn": 65001, "local_asn": 65000},
        )
        peer_id = resp.json()["bgp_peer"]["id"]
        resp2 = client.delete(f"/api/v1/bgp-peers/{peer_id}")
        assert resp2.status_code == 204
        resp3 = client.get(f"/api/v1/bgp-peers/{peer_id}")
        assert resp3.status_code == 404

    def test_update_bgp_peer_state(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.post(
            "/api/v1/bgp-peers",
            json={"router_id": router["id"], "peer_ip": "1.1.1.1", "peer_asn": 65001, "local_asn": 65000},
        )
        peer_id = resp.json()["bgp_peer"]["id"]
        resp2 = client.put(
            f"/api/v1/bgp-peers/{peer_id}/state",
            json={"state": "established"},
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["bgp_peer"]["state"] == "established"

    def test_create_peer_nonexistent_router(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/bgp-peers",
            json={"router_id": "rtr_999", "peer_ip": "1.1.1.1", "peer_asn": 65001, "local_asn": 65000},
        )
        assert resp.status_code == 404
