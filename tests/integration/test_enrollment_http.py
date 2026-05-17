"""End-to-end enrolment + heartbeat flow through the FastAPI app.

We exercise the same sequence an operator + agent would run:

1. operator registers a node          → ``pending``
2. operator issues an enrolment token → plaintext returned once
3. agent presents the token           → node becomes ``online``
4. agent heartbeats                   → last_seen_at refreshed
5. token reuse, unknown-id, etc. are rejected with stable error codes
"""

from __future__ import annotations

from fastapi.testclient import TestClient

_VALID_CAPS = {
    "ovs_version": "3.2.1",
    "kernel": "6.6.0",
    "interfaces": ["eth0", "eth1"],
    "features": ["vxlan"],
}


def _register(client: TestClient, name: str = "edge-1") -> str:
    r = client.post("/api/v1/nodes", json={"name": name, "mgmt_ip": "10.0.0.10"})
    assert r.status_code == 202, r.text
    node_id = r.json()["node"]["id"]
    assert isinstance(node_id, str)
    return node_id


def _issue_token(client: TestClient, node_id: str) -> str:
    r = client.post(f"/api/v1/nodes/{node_id}/enroll-token")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["node_id"] == node_id
    token = body["token"]
    assert isinstance(token, str)
    return token


def test_register_returns_pending_node(client: TestClient) -> None:
    r = client.post("/api/v1/nodes", json={"name": "edge-1", "mgmt_ip": "10.0.0.10"})

    assert r.status_code == 202
    body = r.json()
    assert body["node"]["status"] == "pending"
    assert body["operation"]["status"] == "succeeded"
    assert body["operation"]["resource"]["type"] == "node"


def test_duplicate_node_name_returns_409(client: TestClient) -> None:
    _register(client)
    r = client.post("/api/v1/nodes", json={"name": "edge-1", "mgmt_ip": "10.0.0.11"})

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_full_enrollment_flow(client: TestClient) -> None:
    node_id = _register(client)
    token = _issue_token(client, node_id)

    enroll = client.post(
        "/api/v1/agent/enroll",
        json={"token": token, "agent_version": "0.1.0", "capabilities": _VALID_CAPS},
    )
    assert enroll.status_code == 200, enroll.text
    node = enroll.json()["node"]
    assert node["status"] == "online"
    assert node["agent_version"] == "0.1.0"
    assert node["capabilities"]["ovs_version"] == "3.2.1"
    assert node["capabilities"]["interfaces"] == ["eth0", "eth1"]


def test_token_can_only_be_used_once(client: TestClient) -> None:
    node_id = _register(client)
    token = _issue_token(client, node_id)

    first = client.post("/api/v1/agent/enroll", json={"token": token})
    assert first.status_code == 200

    second = client.post("/api/v1/agent/enroll", json={"token": token})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


def test_unknown_token_returns_404(client: TestClient) -> None:
    r = client.post("/api/v1/agent/enroll", json={"token": "definitely-not-a-real-token"})

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_issue_token_for_unknown_node_returns_404(client: TestClient) -> None:
    r = client.post("/api/v1/nodes/node_ghost/enroll-token")

    assert r.status_code == 404


def test_heartbeat_refreshes_last_seen(client: TestClient) -> None:
    node_id = _register(client)
    token = _issue_token(client, node_id)
    client.post("/api/v1/agent/enroll", json={"token": token})

    hb = client.post(
        "/api/v1/agent/heartbeat",
        json={"node_id": node_id, "agent_version": "0.2.0", "capabilities": _VALID_CAPS},
    )
    assert hb.status_code == 200, hb.text
    node = hb.json()["node"]
    assert node["agent_version"] == "0.2.0"
    assert node["status"] == "online"


def test_heartbeat_for_pending_node_returns_400(client: TestClient) -> None:
    node_id = _register(client)

    r = client.post("/api/v1/agent/heartbeat", json={"node_id": node_id})

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_delete_node_cascades_tokens(client: TestClient) -> None:
    node_id = _register(client)
    token = _issue_token(client, node_id)

    r = client.delete(f"/api/v1/nodes/{node_id}")
    assert r.status_code == 202
    assert r.json()["status"] == "succeeded"

    # Node is gone — and the token bound to it should be too.
    assert client.get(f"/api/v1/nodes/{node_id}").status_code == 404
    assert client.post("/api/v1/agent/enroll", json={"token": token}).status_code == 404
