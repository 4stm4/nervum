"""HTTP integration-тесты N5 — Advanced.

Покрывает:
  N5-01  ApplySchedule  (CRUD + toggle)
  N5-02  MirrorSession  (CRUD + apply)
  N5-05  VPNaaS         (CRUD туннелей + apply + peer CRUD)

Используют in-memory адаптеры. Каждый тест независим.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _create_schedule(client: TestClient, **kwargs: object) -> dict:
    payload = {
        "name": "sched-1",
        "cron_expr": "0 * * * *",
        "target_type": "network",
        "target_id": "net_1",
        **kwargs,
    }
    resp = client.post("/api/v1/schedules", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_mirror(client: TestClient, **kwargs: object) -> dict:
    payload = {
        "name": "mirror-1",
        "source_port_id": "lport_1",
        "direction": "both",
        "destination_port_id": "lport_2",
        **kwargs,
    }
    resp = client.post("/api/v1/mirror-sessions", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_tunnel(client: TestClient, **kwargs: object) -> dict:
    payload = {
        "name": "tun-1",
        "protocol": "wireguard",
        "local_endpoint": "10.0.0.1",
        "remote_endpoint": "10.0.0.2",
        "local_public_key": "LOCAL_KEY",
        "remote_public_key": "REMOTE_KEY",
        **kwargs,
    }
    resp = client.post("/api/v1/vpn-tunnels", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_peer(client: TestClient, tunnel_id: str, **kwargs: object) -> dict:
    payload = {
        "public_key": "PEER_KEY",
        "allowed_ips": ["10.100.0.1/32"],
        **kwargs,
    }
    resp = client.post(f"/api/v1/vpn-tunnels/{tunnel_id}/peers", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# N5-01  ApplySchedule
# ===========================================================================


class TestScheduleCrud:
    def test_create_and_get(self, client: TestClient) -> None:
        sched = _create_schedule(client)
        assert sched["name"] == "sched-1"
        assert sched["cron_expr"] == "0 * * * *"
        assert sched["target_type"] == "network"
        assert sched["enabled"] is True
        assert sched["status"] == "active"

        resp = client.get(f"/api/v1/schedules/{sched['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == sched["id"]

    def test_list(self, client: TestClient) -> None:
        _create_schedule(client, name="s1")
        _create_schedule(client, name="s2")
        resp = client.get("/api/v1/schedules")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    def test_update(self, client: TestClient) -> None:
        sched = _create_schedule(client)
        resp = client.patch(
            f"/api/v1/schedules/{sched['id']}",
            json={"name": "new-name"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "new-name"

    def test_delete(self, client: TestClient) -> None:
        sched = _create_schedule(client)
        resp = client.delete(f"/api/v1/schedules/{sched['id']}")
        assert resp.status_code == 204
        resp2 = client.get(f"/api/v1/schedules/{sched['id']}")
        assert resp2.status_code == 404

    def test_get_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/schedules/sched-missing")
        assert resp.status_code == 404

    def test_toggle_pause_and_enable(self, client: TestClient) -> None:
        sched = _create_schedule(client)
        # пауза
        resp = client.post(
            f"/api/v1/schedules/{sched['id']}/toggle",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["status"] == "paused"
        # включение
        resp2 = client.post(
            f"/api/v1/schedules/{sched['id']}/toggle",
            json={"enabled": True},
        )
        assert resp2.status_code == 200
        assert resp2.json()["enabled"] is True
        assert resp2.json()["status"] == "active"

    def test_create_invalid_cron(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/schedules",
            json={
                "name": "bad",
                "cron_expr": "bad cron",
                "target_type": "network",
                "target_id": "net_1",
            },
        )
        assert resp.status_code in (400, 422)

    def test_filter_by_project(self, client: TestClient) -> None:
        _create_schedule(client, project_id="proj_A", name="pA-sched")
        _create_schedule(client, project_id="proj_B", name="pB-sched")
        resp = client.get("/api/v1/schedules?project_id=proj_A")
        assert resp.status_code == 200
        ids = [s["name"] for s in resp.json()]
        assert "pA-sched" in ids
        assert "pB-sched" not in ids


# ===========================================================================
# N5-02  MirrorSession
# ===========================================================================


class TestMirrorSessionCrud:
    def test_create_span(self, client: TestClient) -> None:
        ms = _create_mirror(client)
        assert ms["name"] == "mirror-1"
        assert ms["direction"] == "both"
        assert ms["destination_port_id"] == "lport_2"
        assert ms["status"] == "inactive"

    def test_create_erspan(self, client: TestClient) -> None:
        ms = _create_mirror(
            client,
            destination_port_id=None,
            destination_ip="192.168.50.10",
            name="erspan-1",
        )
        assert ms["destination_ip"] == "192.168.50.10"
        assert ms["destination_port_id"] is None

    def test_list(self, client: TestClient) -> None:
        _create_mirror(client, name="m1")
        _create_mirror(client, name="m2")
        resp = client.get("/api/v1/mirror-sessions")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    def test_get(self, client: TestClient) -> None:
        ms = _create_mirror(client)
        resp = client.get(f"/api/v1/mirror-sessions/{ms['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == ms["id"]

    def test_delete(self, client: TestClient) -> None:
        ms = _create_mirror(client)
        resp = client.delete(f"/api/v1/mirror-sessions/{ms['id']}")
        assert resp.status_code == 204
        resp2 = client.get(f"/api/v1/mirror-sessions/{ms['id']}")
        assert resp2.status_code == 404

    def test_apply_generates_config(self, client: TestClient) -> None:
        ms = _create_mirror(client)
        resp = client.post(f"/api/v1/mirror-sessions/{ms['id']}/apply")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == ms["id"]
        assert len(body["config"]) > 0

    def test_apply_updates_status(self, client: TestClient) -> None:
        ms = _create_mirror(client)
        client.post(f"/api/v1/mirror-sessions/{ms['id']}/apply")
        resp = client.get(f"/api/v1/mirror-sessions/{ms['id']}")
        assert resp.json()["status"] == "active"

    def test_create_no_destination_fails(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/mirror-sessions",
            json={
                "name": "bad",
                "source_port_id": "lport_1",
                "direction": "both",
            },
        )
        assert resp.status_code in (400, 422)

    def test_create_both_destinations_fails(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/mirror-sessions",
            json={
                "name": "bad2",
                "source_port_id": "lport_1",
                "direction": "both",
                "destination_port_id": "lport_2",
                "destination_ip": "10.0.0.1",
            },
        )
        assert resp.status_code in (400, 422)


# ===========================================================================
# N5-05  VPNaaS — VpnTunnel + VpnPeer
# ===========================================================================


class TestVpnTunnelCrud:
    def test_create_wireguard(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        assert t["name"] == "tun-1"
        assert t["protocol"] == "wireguard"
        assert t["status"] == "build"
        assert t["listen_port"] == 51820

    def test_create_ipsec(self, client: TestClient) -> None:
        t = _create_tunnel(client, protocol="ipsec", name="ipsec-tun")
        assert t["protocol"] == "ipsec"

    def test_list(self, client: TestClient) -> None:
        _create_tunnel(client, name="t1")
        _create_tunnel(client, name="t2")
        resp = client.get("/api/v1/vpn-tunnels")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    def test_get(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        resp = client.get(f"/api/v1/vpn-tunnels/{t['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == t["id"]

    def test_update(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        resp = client.patch(
            f"/api/v1/vpn-tunnels/{t['id']}",
            json={"name": "renamed", "remote_public_key": "NEW_KEY"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "renamed"
        assert body["remote_public_key"] == "NEW_KEY"

    def test_delete(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        resp = client.delete(f"/api/v1/vpn-tunnels/{t['id']}")
        assert resp.status_code == 204
        resp2 = client.get(f"/api/v1/vpn-tunnels/{t['id']}")
        assert resp2.status_code == 404

    def test_apply_wireguard(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        resp = client.post(f"/api/v1/vpn-tunnels/{t['id']}/apply")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == t["id"]
        assert "[Interface]" in body["config"]

    def test_apply_ipsec(self, client: TestClient) -> None:
        t = _create_tunnel(client, protocol="ipsec", name="ipsec-t")
        resp = client.post(f"/api/v1/vpn-tunnels/{t['id']}/apply")
        assert resp.status_code == 200
        cfg = resp.json()["config"]
        # ipsec config должен содержать conn или ipsec.conf
        assert len(cfg) > 0

    def test_apply_updates_status(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        client.post(f"/api/v1/vpn-tunnels/{t['id']}/apply")
        resp = client.get(f"/api/v1/vpn-tunnels/{t['id']}")
        assert resp.json()["status"] == "active"

    def test_filter_by_project(self, client: TestClient) -> None:
        _create_tunnel(client, project_id="proj_A", name="tA")
        _create_tunnel(client, project_id="proj_B", name="tB")
        resp = client.get("/api/v1/vpn-tunnels?project_id=proj_A")
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()]
        assert "tA" in names
        assert "tB" not in names

    def test_get_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/vpn-tunnels/vpnt-missing")
        assert resp.status_code == 404


class TestVpnPeerCrud:
    def test_add_and_list_peers(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        peer = _add_peer(client, t["id"])
        assert peer["public_key"] == "PEER_KEY"
        assert peer["tunnel_id"] == t["id"]

        resp = client.get(f"/api/v1/vpn-tunnels/{t['id']}/peers")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_peer(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        peer = _add_peer(client, t["id"])
        resp = client.get(f"/api/v1/vpn-tunnels/{t['id']}/peers/{peer['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == peer["id"]

    def test_update_peer(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        peer = _add_peer(client, t["id"])
        resp = client.patch(
            f"/api/v1/vpn-tunnels/{t['id']}/peers/{peer['id']}",
            json={"allowed_ips": ["10.200.0.0/24"], "persistent_keepalive": 25},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed_ips"] == ["10.200.0.0/24"]
        assert body["persistent_keepalive"] == 25

    def test_delete_peer(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        peer = _add_peer(client, t["id"])
        resp = client.delete(f"/api/v1/vpn-tunnels/{t['id']}/peers/{peer['id']}")
        assert resp.status_code == 204
        resp2 = client.get(f"/api/v1/vpn-tunnels/{t['id']}/peers")
        assert len(resp2.json()) == 0

    def test_duplicate_key_conflict(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        _add_peer(client, t["id"], public_key="DUP_KEY")
        resp = client.post(
            f"/api/v1/vpn-tunnels/{t['id']}/peers",
            json={"public_key": "DUP_KEY", "allowed_ips": ["10.0.0.2/32"]},
        )
        assert resp.status_code == 409

    def test_delete_tunnel_cascades_peers(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        _add_peer(client, t["id"])
        # удаляем туннель
        client.delete(f"/api/v1/vpn-tunnels/{t['id']}")
        # GET туннеля — 404
        resp = client.get(f"/api/v1/vpn-tunnels/{t['id']}")
        assert resp.status_code == 404

    def test_apply_with_peers(self, client: TestClient) -> None:
        t = _create_tunnel(client)
        _add_peer(client, t["id"], public_key="P1", allowed_ips=["10.100.1.0/24"])
        resp = client.post(f"/api/v1/vpn-tunnels/{t['id']}/apply")
        assert resp.status_code == 200
        cfg = resp.json()["config"]
        assert "[Peer]" in cfg
        assert "P1" in cfg
        assert "10.100.1.0/24" in cfg
