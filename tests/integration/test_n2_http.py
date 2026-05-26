"""HTTP integration-тесты N2 — SecurityPolicy и TrunkPort.

Используют in-memory адаптеры. Каждый тест независим: контейнер
пересоздаётся через фикстуры из conftest.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CountingIdFactory, FrozenClock, SequentialTokenFactory


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def node_id(client: TestClient) -> str:
    """Создаёт узел и возвращает его id — нужен для TrunkPort."""
    resp = client.post(
        "/api/v1/nodes",
        json={"name": "test-node", "ip_address": "10.0.0.1"},
    )
    assert resp.status_code in (200, 201, 202), resp.text
    data = resp.json()
    # Роутер возвращает либо node.id, либо operation с resource_id
    if "node" in data:
        return data["node"]["id"]
    # Некоторые роутеры возвращают operation
    return data.get("operation", {}).get("resource_id", "node_1")


# ---------------------------------------------------------------------------
# SecurityPolicy HTTP-тесты
# ---------------------------------------------------------------------------


class TestSecurityPolicyHTTP:
    def test_create_policy(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/security-policies",
            json={"name": "test-policy", "description": "test"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "security_policy" in body
        policy = body["security_policy"]
        assert policy["name"] == "test-policy"
        assert policy["status"] == "draft"
        assert isinstance(policy["rules"], list)
        assert policy["id"].startswith("spol_")

    def test_list_policies_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/security-policies")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_list_policies_returns_created(self, client: TestClient) -> None:
        client.post("/api/v1/security-policies", json={"name": "p1"})
        client.post("/api/v1/security-policies", json={"name": "p2"})
        resp = client.get("/api/v1/security-policies")
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 2

    def test_get_policy(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/security-policies", json={"name": "p"}
        ).json()["security_policy"]
        resp = client.get(f"/api/v1/security-policies/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["security_policy"]["id"] == created["id"]

    def test_get_missing_policy_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/security-policies/spol_notexist")
        assert resp.status_code == 404

    def test_update_policy(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/security-policies", json={"name": "old-name"}
        ).json()["security_policy"]
        resp = client.patch(
            f"/api/v1/security-policies/{created['id']}",
            json={"name": "new-name"},
        )
        assert resp.status_code == 200
        assert resp.json()["security_policy"]["name"] == "new-name"

    def test_delete_policy(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/security-policies", json={"name": "p"}
        ).json()["security_policy"]
        resp = client.delete(f"/api/v1/security-policies/{created['id']}")
        assert resp.status_code == 204
        # Повторный GET должен вернуть 404
        resp2 = client.get(f"/api/v1/security-policies/{created['id']}")
        assert resp2.status_code == 404

    def test_delete_missing_policy_returns_404(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/security-policies/spol_x")
        assert resp.status_code == 404

    def test_add_rule_to_policy(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/security-policies", json={"name": "p"}
        ).json()["security_policy"]
        resp = client.post(
            f"/api/v1/security-policies/{created['id']}/rules",
            json={
                "priority": 100,
                "direction": "ingress",
                "action": "allow",
                "source_type": "cidr",
                "source_value": "10.0.0.0/8",
            },
        )
        assert resp.status_code == 201, resp.text
        policy = resp.json()["security_policy"]
        assert len(policy["rules"]) == 1
        rule = policy["rules"][0]
        assert rule["priority"] == 100
        assert rule["direction"] == "ingress"
        assert rule["action"] == "allow"
        assert rule["packet_count"] == 0
        assert rule["byte_count"] == 0

    def test_add_rule_invalid_priority_returns_422(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/security-policies", json={"name": "p"}
        ).json()["security_policy"]
        # priority=0 не валиден
        resp = client.post(
            f"/api/v1/security-policies/{created['id']}/rules",
            json={"priority": 0, "direction": "ingress", "action": "allow"},
        )
        assert resp.status_code in (400, 422)

    def test_remove_rule_from_policy(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/security-policies", json={"name": "p"}
        ).json()["security_policy"]
        with_rule = client.post(
            f"/api/v1/security-policies/{created['id']}/rules",
            json={"priority": 100, "direction": "ingress", "action": "allow"},
        ).json()["security_policy"]
        rule_id = with_rule["rules"][0]["rule_id"]
        resp = client.delete(
            f"/api/v1/security-policies/{created['id']}/rules/{rule_id}"
        )
        assert resp.status_code == 204

    def test_compile_policy(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/security-policies", json={"name": "p"}
        ).json()["security_policy"]
        resp = client.post(f"/api/v1/security-policies/{created['id']}/compile")
        assert resp.status_code == 200, resp.text
        policy = resp.json()["security_policy"]
        assert policy["status"] == "compiled"
        assert policy["compiled_ruleset"] is not None
        assert "nft" in policy["compiled_ruleset"]

    def test_apply_policy_requires_compiled(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/security-policies", json={"name": "p"}
        ).json()["security_policy"]
        # Попытка применить до компиляции должна вернуть ошибку
        resp = client.post(f"/api/v1/security-policies/{created['id']}/apply")
        assert resp.status_code in (400, 422, 409)

    def test_apply_compiled_policy(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/security-policies", json={"name": "p"}
        ).json()["security_policy"]
        client.post(f"/api/v1/security-policies/{created['id']}/compile")
        resp = client.post(f"/api/v1/security-policies/{created['id']}/apply")
        assert resp.status_code == 200
        policy = resp.json()["security_policy"]
        assert policy["status"] == "applied"
        assert policy["applied_at"] is not None

    def test_list_policies_filter_by_project(self, client: TestClient) -> None:
        client.post(
            "/api/v1/security-policies",
            json={"name": "p1", "project_id": "proj_1"},
        )
        client.post("/api/v1/security-policies", json={"name": "p2"})
        resp = client.get("/api/v1/security-policies?project_id=proj_1")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "p1"

    def test_rules_sorted_by_priority(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/security-policies", json={"name": "p"}
        ).json()["security_policy"]
        policy_id = created["id"]
        client.post(
            f"/api/v1/security-policies/{policy_id}/rules",
            json={"priority": 300, "direction": "ingress", "action": "deny"},
        )
        client.post(
            f"/api/v1/security-policies/{policy_id}/rules",
            json={"priority": 100, "direction": "ingress", "action": "allow"},
        )
        resp = client.get(f"/api/v1/security-policies/{policy_id}")
        rules = resp.json()["security_policy"]["rules"]
        priorities = [r["priority"] for r in rules]
        assert priorities == sorted(priorities)


# ---------------------------------------------------------------------------
# TrunkPort HTTP-тесты
# ---------------------------------------------------------------------------


class TestTrunkPortHTTP:
    def _create_node(self, client: TestClient) -> str:
        """Регистрирует узел через enroll-токен или прямой endpoint."""
        # Используем enrollment: сначала issue token, затем enroll
        token_resp = client.post(
            "/api/v1/nodes/enrollment-tokens",
            json={"node_name": "test-node"},
        )
        if token_resp.status_code not in (200, 201, 202):
            # Запасной вариант: проверим если есть endpoint создания узла
            pytest.skip("enrollment endpoint not reachable in this config")
        token_data = token_resp.json()
        # Симулируем enroll (агент передаёт plaintext)
        plaintext = token_data.get("token") or token_data.get("enrollment_token", {}).get("plaintext")
        if not plaintext:
            pytest.skip("cannot extract token plaintext")
        enroll_resp = client.post(
            "/api/v1/nodes/enroll",
            json={"token": plaintext, "ip_address": "10.0.0.1"},
        )
        if enroll_resp.status_code not in (200, 201, 202):
            pytest.skip("enroll endpoint failed")
        data = enroll_resp.json()
        if "node" in data:
            return data["node"]["id"]
        return data.get("node_id", "")

    def _make_node_directly(self, client: TestClient) -> str:
        """Создаёт узел напрямую через регистрацию агента."""
        resp = client.post(
            "/api/v1/nodes",
            json={"name": "trunk-test-node", "ip_address": "10.0.0.2"},
        )
        if resp.status_code in (200, 201, 202):
            data = resp.json()
            if "node" in data:
                return data["node"]["id"]
        # Иначе пробуем enrollment flow
        return self._create_node(client)

    def test_create_trunk_port(
        self,
        client: TestClient,
        container: "object",
    ) -> None:
        """Создаёт trunk-порт; узел создаётся через контейнер напрямую."""
        from datetime import UTC, datetime
        from sdn_controller.core.entities import Node
        from sdn_controller.core.value_objects.ids import NodeId
        from tests.conftest import FrozenClock

        # Вставляем узел в репозиторий напрямую (обходим HTTP-round-trip)
        import asyncio

        clock = FrozenClock()
        now = clock.now()
        node = Node(
            id=NodeId("node_1"),
            name="trunk-node",
            mgmt_ip="10.0.0.1",
            created_at=now,
            updated_at=now,
        )

        async def _save() -> None:
            await container.nodes_repo.save(node)  # type: ignore[attr-defined]

        asyncio.run(_save())

        resp = client.post(
            "/api/v1/trunk-ports",
            json={
                "name": "trunk-0",
                "node_id": "node_1",
                "vlan_ids": [10, 20, 30],
                "native_vlan": 10,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        port = body["trunk_port"]
        assert port["name"] == "trunk-0"
        assert port["vlan_ids"] == [10, 20, 30]
        assert port["native_vlan"] == 10
        assert port["id"].startswith("tport_")

    def test_create_trunk_port_missing_node(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/trunk-ports",
            json={"name": "t", "node_id": "no_such_node", "vlan_ids": [10]},
        )
        assert resp.status_code == 404

    def test_list_trunk_ports_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/trunk-ports")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_get_trunk_port_missing_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/v1/trunk-ports/tport_x")
        assert resp.status_code == 404

    def test_delete_trunk_port_missing_returns_404(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/trunk-ports/tport_x")
        assert resp.status_code == 404

    def test_update_trunk_port_missing_returns_404(self, client: TestClient) -> None:
        resp = client.patch("/api/v1/trunk-ports/tport_x", json={"name": "new"})
        assert resp.status_code == 404

    def test_trunk_port_vlan_ids_sorted(
        self,
        client: TestClient,
        container: "object",
    ) -> None:
        import asyncio
        from sdn_controller.core.entities import Node
        from sdn_controller.core.value_objects.ids import NodeId
        from tests.conftest import FrozenClock

        clock = FrozenClock()
        now = clock.now()
        node = Node(
            id=NodeId("node_2"),
            name="node2",
            mgmt_ip="10.0.0.2",
            created_at=now,
            updated_at=now,
        )

        async def _save() -> None:
            await container.nodes_repo.save(node)  # type: ignore[attr-defined]

        asyncio.run(_save())

        resp = client.post(
            "/api/v1/trunk-ports",
            json={"name": "t", "node_id": "node_2", "vlan_ids": [30, 10, 20]},
        )
        assert resp.status_code == 201
        vlan_ids = resp.json()["trunk_port"]["vlan_ids"]
        assert vlan_ids == sorted(vlan_ids)
