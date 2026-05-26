"""HTTP integration-тесты N4 — Governance & Scale.

Покрывает:
  N4-01  ProjectQuota  (get / set / delete / usage)
  N4-02  Preflight     (router preflight)
  N4-03  ResourceSnapshot (list / create / get / delete)
  N4-04  GatewayBond   (CRUD + apply)
  N4-05  RetentionPolicy (set / list / get / delete)
  N4-06  LoadBalancer + Listener + Pool + Member (CRUD + apply)
  N4-07  HealthMonitor (CRUD)

Используют in-memory адаптеры. Каждый тест независим: контейнер
пересоздаётся через фикстуры из conftest.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _create_lb(client: TestClient, **kwargs: object) -> dict:
    payload = {
        "name": "lb1",
        "vip_address": "10.0.0.100",
        "vip_network_id": "net_1",
        **kwargs,
    }
    resp = client.post("/api/v1/load-balancers", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_pool(client: TestClient, lb_id: str, **kwargs: object) -> dict:
    payload = {"name": "pool1", "lb_id": lb_id, "protocol": "tcp", **kwargs}
    resp = client.post("/api/v1/lb-pools", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_listener(client: TestClient, lb_id: str, **kwargs: object) -> dict:
    payload = {
        "name": "lis1",
        "lb_id": lb_id,
        "protocol": "tcp",
        "protocol_port": 80,
        **kwargs,
    }
    resp = client.post("/api/v1/lb-listeners", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_bond(client: TestClient, **kwargs: object) -> dict:
    payload = {
        "name": "bond0",
        "node_id": "node_1",
        "bond_name": "bond0",
        "mode": "lacp",
        "members": ["eth0", "eth1"],
        **kwargs,
    }
    resp = client.post("/api/v1/gateway-bonds", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_router(client: TestClient, **kwargs: object) -> dict:
    payload = {"name": "gw", **kwargs}
    resp = client.post("/api/v1/routers", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["router"]


# ---------------------------------------------------------------------------
# N4-01  ProjectQuota
# ---------------------------------------------------------------------------


class TestProjectQuotaHTTP:
    def test_get_quota_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/quotas/proj_1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == "proj_1"
        assert body["limits"] == {}

    def test_set_quota(self, client: TestClient) -> None:
        resp = client.put(
            "/api/v1/quotas/proj_1",
            json={"resource": "routers", "limit": 5},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["project_id"] == "proj_1"
        assert body["limits"]["routers"] == 5
        assert body["id"].startswith("pquota_")

    def test_set_quota_multiple_resources(self, client: TestClient) -> None:
        client.put("/api/v1/quotas/proj_1", json={"resource": "routers", "limit": 5})
        client.put("/api/v1/quotas/proj_1", json={"resource": "load_balancers", "limit": 10})
        resp = client.get("/api/v1/quotas/proj_1")
        assert resp.status_code == 200
        limits = resp.json()["limits"]
        assert limits["routers"] == 5
        assert limits["load_balancers"] == 10

    def test_set_quota_remove_limit(self, client: TestClient) -> None:
        # сначала устанавливаем лимит
        client.put("/api/v1/quotas/proj_1", json={"resource": "routers", "limit": 5})
        # затем снимаем его (limit=None → remove_limit)
        resp = client.put(
            "/api/v1/quotas/proj_1",
            json={"resource": "routers", "limit": None},
        )
        assert resp.status_code == 200, resp.text
        # ключ удаляется из словаря лимитов
        assert "routers" not in resp.json()["limits"]

    def test_delete_quota(self, client: TestClient) -> None:
        client.put("/api/v1/quotas/proj_1", json={"resource": "routers", "limit": 5})
        resp = client.delete("/api/v1/quotas/proj_1")
        assert resp.status_code == 204
        # после удаления — пустой ответ
        resp2 = client.get("/api/v1/quotas/proj_1")
        assert resp2.status_code == 200
        assert resp2.json()["limits"] == {}

    def test_usage_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/v1/quotas/proj_1/usage")
        assert resp.status_code == 200
        body = resp.json()
        # поле usage присутствует
        assert "usage" in body or "project_id" in body


# ---------------------------------------------------------------------------
# N4-02  Preflight
# ---------------------------------------------------------------------------


class TestPreflightHTTP:
    def test_preflight_router_no_issues(self, client: TestClient) -> None:
        router = _create_router(client)
        resp = client.post(f"/api/v1/preflight/router/{router['id']}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["router_id"] == router["id"]
        assert isinstance(body["issues"], list)

    def test_preflight_router_not_found(self, client: TestClient) -> None:
        resp = client.post("/api/v1/preflight/router/rtr_999")
        assert resp.status_code == 404

    def test_preflight_router_with_ha(self, client: TestClient) -> None:
        # VRRP-маршрутизатор с неполными настройками — preflight должен вернуть предупреждения
        router = _create_router(
            client,
            ha_mode="vrrp",
            vrrp_priority=100,
            vrrp_vrid=10,
        )
        resp = client.post(f"/api/v1/preflight/router/{router['id']}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "issues" in body


# ---------------------------------------------------------------------------
# N4-03  ResourceSnapshot
# ---------------------------------------------------------------------------


class TestResourceSnapshotHTTP:
    def test_list_snapshots_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/snapshots")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_snapshot(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/snapshots",
            json={"project_id": "proj_1", "label": "before-upgrade"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["project_id"] == "proj_1"
        assert body["label"] == "before-upgrade"
        assert body["version"] == 1
        assert body["id"].startswith("rsnap_")

    def test_create_snapshot_increments_version(self, client: TestClient) -> None:
        client.post("/api/v1/snapshots", json={"project_id": "proj_1", "label": "v1"})
        resp = client.post(
            "/api/v1/snapshots", json={"project_id": "proj_1", "label": "v2"}
        )
        assert resp.status_code == 201
        assert resp.json()["version"] == 2

    def test_get_snapshot(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/snapshots",
            json={"project_id": "proj_1", "label": "snap"},
        )
        snap_id = resp.json()["id"]
        resp2 = client.get(f"/api/v1/snapshots/{snap_id}")
        assert resp2.status_code == 200
        body = resp2.json()
        assert body["id"] == snap_id
        assert "payload" in body

    def test_get_snapshot_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/snapshots/rsnap_999")
        assert resp.status_code == 404

    def test_delete_snapshot(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/snapshots",
            json={"project_id": "proj_1", "label": "snap"},
        )
        snap_id = resp.json()["id"]
        resp2 = client.delete(f"/api/v1/snapshots/{snap_id}")
        assert resp2.status_code == 204
        resp3 = client.get(f"/api/v1/snapshots/{snap_id}")
        assert resp3.status_code == 404

    def test_list_snapshots_by_project(self, client: TestClient) -> None:
        client.post("/api/v1/snapshots", json={"project_id": "proj_1", "label": "a"})
        client.post("/api/v1/snapshots", json={"project_id": "proj_2", "label": "b"})
        resp = client.get("/api/v1/snapshots?project_id=proj_1")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["project_id"] == "proj_1"


# ---------------------------------------------------------------------------
# N4-04  GatewayBond
# ---------------------------------------------------------------------------


class TestGatewayBondHTTP:
    def test_create_bond(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/gateway-bonds",
            json={
                "name": "bond0",
                "node_id": "node_1",
                "bond_name": "bond0",
                "mode": "lacp",
                "members": ["eth0", "eth1"],
                "mtu": 9000,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "bond0"
        assert body["mode"] == "lacp"
        assert body["mtu"] == 9000
        assert body["id"].startswith("gbond_")

    def test_list_bonds_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/gateway-bonds")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_bonds(self, client: TestClient) -> None:
        _create_bond(client, name="b1", node_id="node_1", bond_name="bond0")
        _create_bond(client, name="b2", node_id="node_2", bond_name="bond0")
        resp = client.get("/api/v1/gateway-bonds")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_bonds_filter_by_node(self, client: TestClient) -> None:
        _create_bond(client, name="b1", node_id="node_1", bond_name="bond0")
        _create_bond(client, name="b2", node_id="node_2", bond_name="bond0")
        resp = client.get("/api/v1/gateway-bonds?node_id=node_1")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["node_id"] == "node_1"

    def test_get_bond(self, client: TestClient) -> None:
        bond = _create_bond(client)
        resp = client.get(f"/api/v1/gateway-bonds/{bond['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == bond["id"]

    def test_get_bond_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/gateway-bonds/gbond_999")
        assert resp.status_code == 404

    def test_update_bond(self, client: TestClient) -> None:
        bond = _create_bond(client)
        resp = client.patch(
            f"/api/v1/gateway-bonds/{bond['id']}",
            json={"mtu": 1500, "name": "renamed"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mtu"] == 1500
        assert body["name"] == "renamed"

    def test_delete_bond(self, client: TestClient) -> None:
        bond = _create_bond(client)
        resp = client.delete(f"/api/v1/gateway-bonds/{bond['id']}")
        assert resp.status_code == 204
        resp2 = client.get(f"/api/v1/gateway-bonds/{bond['id']}")
        assert resp2.status_code == 404

    def test_apply_bond(self, client: TestClient) -> None:
        bond = _create_bond(client)
        resp = client.post(f"/api/v1/gateway-bonds/{bond['id']}/apply")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["applied_config"] is not None
        assert body["applied_at"] is not None
        # конфиг содержит netplan-секцию
        assert "bond0" in body["applied_config"]

    def test_apply_bond_mode_none(self, client: TestClient) -> None:
        bond = _create_bond(client, mode="none", members=[])
        resp = client.post(f"/api/v1/gateway-bonds/{bond['id']}/apply")
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied_config"] is not None


# ---------------------------------------------------------------------------
# N4-05  RetentionPolicy
# ---------------------------------------------------------------------------


class TestRetentionPolicyHTTP:
    def test_set_retention_policy(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/retention-policies",
            json={"scope": "audit_events", "retention_days": 90},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["scope"] == "audit_events"
        assert body["retention_days"] == 90
        assert body["project_id"] is None
        assert body["id"].startswith("ret_")

    def test_list_policies_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/retention-policies")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_policies(self, client: TestClient) -> None:
        client.post(
            "/api/v1/retention-policies",
            json={"scope": "audit_events", "retention_days": 90},
        )
        client.post(
            "/api/v1/retention-policies",
            json={"scope": "snapshots", "retention_days": 30},
        )
        resp = client.get("/api/v1/retention-policies")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_policy(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/retention-policies",
            json={"scope": "audit_events", "retention_days": 60},
        )
        policy_id = resp.json()["id"]
        resp2 = client.get(f"/api/v1/retention-policies/{policy_id}")
        assert resp2.status_code == 200
        assert resp2.json()["id"] == policy_id

    def test_get_policy_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/retention-policies/ret_999")
        assert resp.status_code == 404

    def test_delete_policy(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/retention-policies",
            json={"scope": "audit_events", "retention_days": 30},
        )
        policy_id = resp.json()["id"]
        resp2 = client.delete(f"/api/v1/retention-policies/{policy_id}")
        assert resp2.status_code == 204
        resp3 = client.get(f"/api/v1/retention-policies/{policy_id}")
        assert resp3.status_code == 404

    def test_upsert_retention_policy(self, client: TestClient) -> None:
        # Повторный POST с тем же scope обновляет политику
        client.post(
            "/api/v1/retention-policies",
            json={"scope": "audit_events", "retention_days": 30},
        )
        resp = client.post(
            "/api/v1/retention-policies",
            json={"scope": "audit_events", "retention_days": 90, "description": "upd"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["retention_days"] == 90

    def test_project_scoped_policy(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/retention-policies",
            json={
                "scope": "audit_events",
                "retention_days": 14,
                "project_id": "proj_42",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["project_id"] == "proj_42"
        assert body["retention_days"] == 14


# ---------------------------------------------------------------------------
# N4-06  LoadBalancer
# ---------------------------------------------------------------------------


class TestLoadBalancerHTTP:
    def test_create_lb(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/load-balancers",
            json={
                "name": "lb1",
                "vip_address": "10.0.0.100",
                "vip_network_id": "net_1",
                "description": "main lb",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "lb1"
        assert body["vip_address"] == "10.0.0.100"
        assert body["status"] == "build"
        assert body["admin_state_up"] is True
        assert body["id"].startswith("lb_")

    def test_list_lbs_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/load-balancers")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_lbs(self, client: TestClient) -> None:
        _create_lb(client, name="lb1")
        _create_lb(client, name="lb2")
        resp = client.get("/api/v1/load-balancers")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_lb(self, client: TestClient) -> None:
        lb = _create_lb(client)
        resp = client.get(f"/api/v1/load-balancers/{lb['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == lb["id"]

    def test_get_lb_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/load-balancers/lb_999")
        assert resp.status_code == 404

    def test_update_lb(self, client: TestClient) -> None:
        lb = _create_lb(client)
        resp = client.patch(
            f"/api/v1/load-balancers/{lb['id']}",
            json={"name": "renamed", "description": "updated"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "renamed"
        assert body["description"] == "updated"

    def test_delete_lb(self, client: TestClient) -> None:
        lb = _create_lb(client)
        resp = client.delete(f"/api/v1/load-balancers/{lb['id']}")
        assert resp.status_code == 204
        resp2 = client.get(f"/api/v1/load-balancers/{lb['id']}")
        assert resp2.status_code == 404

    def test_admin_state_down(self, client: TestClient) -> None:
        lb = _create_lb(client)
        resp = client.put(
            f"/api/v1/load-balancers/{lb['id']}/admin-state?up=false",
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["admin_state_up"] is False

    def test_admin_state_up(self, client: TestClient) -> None:
        lb = _create_lb(client)
        client.put(f"/api/v1/load-balancers/{lb['id']}/admin-state?up=false")
        resp = client.put(f"/api/v1/load-balancers/{lb['id']}/admin-state?up=true")
        assert resp.status_code == 200, resp.text
        assert resp.json()["admin_state_up"] is True

    def test_apply_lb(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        # добавляем участника пула
        client.post(
            "/api/v1/lb-members",
            json={
                "pool_id": pool["id"],
                "address": "192.168.1.10",
                "protocol_port": 8080,
            },
        )
        resp = client.post(f"/api/v1/load-balancers/{lb['id']}/apply")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "active"
        assert body["applied_config"] is not None

    def test_apply_lb_config_contains_haproxy(self, client: TestClient) -> None:
        lb = _create_lb(client)
        _create_pool(client, lb["id"])
        resp = client.post(f"/api/v1/load-balancers/{lb['id']}/apply")
        assert resp.status_code == 200, resp.text
        cfg = resp.json()["applied_config"]
        assert "haproxy" in cfg.lower() or "frontend" in cfg or "backend" in cfg


# ---------------------------------------------------------------------------
# N4-06  LbListener
# ---------------------------------------------------------------------------


class TestLbListenerHTTP:
    def test_create_listener(self, client: TestClient) -> None:
        lb = _create_lb(client)
        resp = client.post(
            "/api/v1/lb-listeners",
            json={
                "name": "http",
                "lb_id": lb["id"],
                "protocol": "http",
                "protocol_port": 80,
                "description": "public http",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["lb_id"] == lb["id"]
        assert body["protocol"] == "http"
        assert body["protocol_port"] == 80
        assert body["id"].startswith("lblis_")

    def test_list_listeners_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/lb-listeners")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_listeners_by_lb(self, client: TestClient) -> None:
        lb1 = _create_lb(client, name="lb1")
        lb2 = _create_lb(client, name="lb2")
        _create_listener(client, lb1["id"], protocol_port=80)
        _create_listener(client, lb2["id"], protocol_port=443)
        resp = client.get(f"/api/v1/lb-listeners?lb_id={lb1['id']}")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["lb_id"] == lb1["id"]

    def test_get_listener(self, client: TestClient) -> None:
        lb = _create_lb(client)
        lis = _create_listener(client, lb["id"])
        resp = client.get(f"/api/v1/lb-listeners/{lis['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == lis["id"]

    def test_get_listener_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/lb-listeners/lblis_999")
        assert resp.status_code == 404

    def test_update_listener(self, client: TestClient) -> None:
        lb = _create_lb(client)
        lis = _create_listener(client, lb["id"])
        resp = client.patch(
            f"/api/v1/lb-listeners/{lis['id']}",
            json={"name": "renamed", "description": "updated"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "renamed"

    def test_delete_listener(self, client: TestClient) -> None:
        lb = _create_lb(client)
        lis = _create_listener(client, lb["id"])
        resp = client.delete(f"/api/v1/lb-listeners/{lis['id']}")
        assert resp.status_code == 204
        resp2 = client.get(f"/api/v1/lb-listeners/{lis['id']}")
        assert resp2.status_code == 404

    def test_create_listener_nonexistent_lb_fails(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/lb-listeners",
            json={
                "name": "lis",
                "lb_id": "lb_999",
                "protocol": "tcp",
                "protocol_port": 80,
            },
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# N4-06  LbPool
# ---------------------------------------------------------------------------


class TestLbPoolHTTP:
    def test_create_pool(self, client: TestClient) -> None:
        lb = _create_lb(client)
        resp = client.post(
            "/api/v1/lb-pools",
            json={
                "name": "pool1",
                "lb_id": lb["id"],
                "protocol": "http",
                "lb_algorithm": "least_connections",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["lb_id"] == lb["id"]
        assert body["lb_algorithm"] == "least_connections"
        assert body["id"].startswith("lbpool_")

    def test_list_pools_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/lb-pools")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_pools_by_lb(self, client: TestClient) -> None:
        lb1 = _create_lb(client, name="lb1")
        lb2 = _create_lb(client, name="lb2")
        _create_pool(client, lb1["id"], name="p1")
        _create_pool(client, lb2["id"], name="p2")
        resp = client.get(f"/api/v1/lb-pools?lb_id={lb1['id']}")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["lb_id"] == lb1["id"]

    def test_get_pool(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.get(f"/api/v1/lb-pools/{pool['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == pool["id"]

    def test_get_pool_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/lb-pools/lbpool_999")
        assert resp.status_code == 404

    def test_update_pool(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.patch(
            f"/api/v1/lb-pools/{pool['id']}",
            json={"lb_algorithm": "source_ip", "session_persistence": "source_ip"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["lb_algorithm"] == "source_ip"
        assert body["session_persistence"] == "source_ip"


    def test_delete_pool(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.delete(f"/api/v1/lb-pools/{pool['id']}")
        assert resp.status_code == 204
        resp2 = client.get(f"/api/v1/lb-pools/{pool['id']}")
        assert resp2.status_code == 404

    def test_create_pool_nonexistent_lb_fails(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/lb-pools",
            json={"name": "p", "lb_id": "lb_999", "protocol": "tcp"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# N4-06  LbMember
# ---------------------------------------------------------------------------


class TestLbMemberHTTP:
    def test_add_member(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.post(
            "/api/v1/lb-members",
            json={
                "pool_id": pool["id"],
                "address": "192.168.1.10",
                "protocol_port": 8080,
                "weight": 2,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["pool_id"] == pool["id"]
        assert body["address"] == "192.168.1.10"
        assert body["weight"] == 2
        assert body["id"].startswith("lbm_")

    def test_list_members_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/lb-members")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_members_by_pool(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool1 = _create_pool(client, lb["id"], name="p1")
        pool2 = _create_pool(client, lb["id"], name="p2")
        client.post(
            "/api/v1/lb-members",
            json={"pool_id": pool1["id"], "address": "10.0.0.1", "protocol_port": 80},
        )
        client.post(
            "/api/v1/lb-members",
            json={"pool_id": pool2["id"], "address": "10.0.0.2", "protocol_port": 80},
        )
        resp = client.get(f"/api/v1/lb-members?pool_id={pool1['id']}")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["pool_id"] == pool1["id"]

    def test_get_member(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.post(
            "/api/v1/lb-members",
            json={"pool_id": pool["id"], "address": "10.0.0.1", "protocol_port": 80},
        )
        member_id = resp.json()["id"]
        resp2 = client.get(f"/api/v1/lb-members/{member_id}")
        assert resp2.status_code == 200
        assert resp2.json()["id"] == member_id

    def test_get_member_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/lb-members/lbm_999")
        assert resp.status_code == 404

    def test_update_member_weight(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.post(
            "/api/v1/lb-members",
            json={"pool_id": pool["id"], "address": "10.0.0.1", "protocol_port": 80},
        )
        member_id = resp.json()["id"]
        resp2 = client.patch(
            f"/api/v1/lb-members/{member_id}",
            json={"weight": 5, "admin_state_up": False},
        )
        assert resp2.status_code == 200, resp2.text
        body = resp2.json()
        assert body["weight"] == 5
        assert body["admin_state_up"] is False

    def test_remove_member(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.post(
            "/api/v1/lb-members",
            json={"pool_id": pool["id"], "address": "10.0.0.1", "protocol_port": 80},
        )
        member_id = resp.json()["id"]
        resp2 = client.delete(f"/api/v1/lb-members/{member_id}")
        assert resp2.status_code == 204
        resp3 = client.get(f"/api/v1/lb-members/{member_id}")
        assert resp3.status_code == 404

    def test_add_member_nonexistent_pool_fails(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/lb-members",
            json={
                "pool_id": "lbpool_999",
                "address": "10.0.0.1",
                "protocol_port": 80,
            },
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# N4-07  HealthMonitor
# ---------------------------------------------------------------------------


class TestHealthMonitorHTTP:
    def test_create_monitor(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.post(
            "/api/v1/health-monitors",
            json={
                "pool_id": pool["id"],
                "check_type": "http",
                "delay": 10,
                "timeout": 5,
                "max_retries": 3,
                "url_path": "/status",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["pool_id"] == pool["id"]
        assert body["check_type"] == "http"
        assert body["url_path"] == "/status"
        assert body["id"].startswith("hm_")

    def test_get_monitor(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.post(
            "/api/v1/health-monitors",
            json={"pool_id": pool["id"], "check_type": "tcp"},
        )
        mon_id = resp.json()["id"]
        resp2 = client.get(f"/api/v1/health-monitors/{mon_id}")
        assert resp2.status_code == 200
        assert resp2.json()["id"] == mon_id

    def test_get_monitor_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/health-monitors/hm_999")
        assert resp.status_code == 404

    def test_update_monitor(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.post(
            "/api/v1/health-monitors",
            json={"pool_id": pool["id"], "check_type": "http"},
        )
        mon_id = resp.json()["id"]
        resp2 = client.patch(
            f"/api/v1/health-monitors/{mon_id}",
            json={"delay": 20, "max_retries": 5, "url_path": "/ping"},
        )
        assert resp2.status_code == 200, resp2.text
        body = resp2.json()
        assert body["delay"] == 20
        assert body["max_retries"] == 5
        assert body["url_path"] == "/ping"

    def test_delete_monitor(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        resp = client.post(
            "/api/v1/health-monitors",
            json={"pool_id": pool["id"], "check_type": "tcp"},
        )
        mon_id = resp.json()["id"]
        resp2 = client.delete(f"/api/v1/health-monitors/{mon_id}")
        assert resp2.status_code == 204
        resp3 = client.get(f"/api/v1/health-monitors/{mon_id}")
        assert resp3.status_code == 404

    def test_create_monitor_nonexistent_pool_fails(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/health-monitors",
            json={"pool_id": "lbpool_999", "check_type": "http"},
        )
        assert resp.status_code == 404

    def test_duplicate_monitor_per_pool_fails(self, client: TestClient) -> None:
        lb = _create_lb(client)
        pool = _create_pool(client, lb["id"])
        client.post(
            "/api/v1/health-monitors",
            json={"pool_id": pool["id"], "check_type": "http"},
        )
        resp = client.post(
            "/api/v1/health-monitors",
            json={"pool_id": pool["id"], "check_type": "tcp"},
        )
        # второй монитор на тот же пул запрещён
        assert resp.status_code in (400, 409, 422), resp.text


# ---------------------------------------------------------------------------
# N4  Полный сценарий — создание LB со всей иерархией и apply
# ---------------------------------------------------------------------------


class TestFullLbScenario:
    def test_full_lb_lifecycle(self, client: TestClient) -> None:
        # 1. создаём балансировщик
        lb_resp = client.post(
            "/api/v1/load-balancers",
            json={
                "name": "prod-lb",
                "vip_address": "10.10.0.1",
                "vip_network_id": "net_pub",
                "provider": "haproxy",
            },
        )
        assert lb_resp.status_code == 201
        lb = lb_resp.json()

        # 2. создаём пул
        pool_resp = client.post(
            "/api/v1/lb-pools",
            json={
                "name": "app-pool",
                "lb_id": lb["id"],
                "protocol": "http",
                "lb_algorithm": "round_robin",
            },
        )
        assert pool_resp.status_code == 201
        pool = pool_resp.json()

        # 3. добавляем listener, указывая default_pool_id
        lis_resp = client.post(
            "/api/v1/lb-listeners",
            json={
                "name": "http-80",
                "lb_id": lb["id"],
                "protocol": "http",
                "protocol_port": 80,
                "default_pool_id": pool["id"],
            },
        )
        assert lis_resp.status_code == 201
        lis = lis_resp.json()
        assert lis["default_pool_id"] == pool["id"]

        # 4. добавляем 2 участника
        for ip in ("10.0.0.10", "10.0.0.11"):
            r = client.post(
                "/api/v1/lb-members",
                json={"pool_id": pool["id"], "address": ip, "protocol_port": 8080},
            )
            assert r.status_code == 201

        # 5. создаём health monitor
        hm_resp = client.post(
            "/api/v1/health-monitors",
            json={
                "pool_id": pool["id"],
                "check_type": "http",
                "url_path": "/health",
                "expected_codes": "200",
            },
        )
        assert hm_resp.status_code == 201

        # 6. apply — генерируем haproxy-конфиг
        apply_resp = client.post(f"/api/v1/load-balancers/{lb['id']}/apply")
        assert apply_resp.status_code == 200, apply_resp.text
        body = apply_resp.json()
        assert body["status"] == "active"
        cfg = body["applied_config"]
        assert cfg is not None
        # конфиг содержит оба IP участников
        assert "10.0.0.10" in cfg
        assert "10.0.0.11" in cfg
