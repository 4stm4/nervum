"""End-to-end tests for the FastAPI northbound API."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_version_returns_semver_and_api_version(client: TestClient) -> None:
    r = client.get("/api/v1/version")
    assert r.status_code == 200
    body = r.json()
    assert body["api_version"] == "v1"
    assert body["version"]  # non-empty


def test_create_network_returns_operation_envelope(client: TestClient) -> None:
    r = client.post(
        "/api/v1/networks",
        json={
            "name": "tenant-a",
            "type": "vxlan",
            "vni": 10100,
            "subnet": {"cidr": "10.100.0.0/24", "gateway": "10.100.0.1"},
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["network"]["name"] == "tenant-a"
    assert body["network"]["vni"] == 10100
    assert body["operation"]["status"] == "succeeded"
    assert body["operation"]["resource"]["type"] == "network"
    op_id = body["operation"]["operation_id"]
    assert body["operation"]["links"]["self"] == f"/api/v1/operations/{op_id}"


def test_create_then_get_network(client: TestClient) -> None:
    created = client.post(
        "/api/v1/networks",
        json={"name": "tenant-b", "type": "vlan", "vlan_id": 200},
    ).json()
    network_id = created["network"]["id"]

    r = client.get(f"/api/v1/networks/{network_id}")
    assert r.status_code == 200
    assert r.json()["name"] == "tenant-b"


def test_get_missing_network_returns_404(client: TestClient) -> None:
    r = client.get("/api/v1/networks/net_does_not_exist")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"


def test_create_network_duplicate_returns_409(client: TestClient) -> None:
    payload = {"name": "tenant-c", "type": "vlan", "vlan_id": 300}
    assert client.post("/api/v1/networks", json=payload).status_code == 202
    r = client.post("/api/v1/networks", json=payload)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_create_network_validation_error_returns_422_or_400(client: TestClient) -> None:
    # Missing required vni for vxlan triggers domain validation (caught after Pydantic).
    r = client.post(
        "/api/v1/networks",
        json={"name": "bad", "type": "vxlan"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_pydantic_validation_returns_422(client: TestClient) -> None:
    r = client.post("/api/v1/networks", json={"name": "x", "type": "nonsense"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "request_validation_error"


def test_operations_list_and_events(client: TestClient) -> None:
    created = client.post(
        "/api/v1/networks",
        json={"name": "tenant-d", "type": "flat"},
    ).json()
    op_id = created["operation"]["operation_id"]

    listed = client.get("/api/v1/operations").json()
    assert any(op["id"] == op_id for op in listed["items"])

    events = client.get(f"/api/v1/operations/{op_id}/events").json()
    statuses = [e["status"] for e in events["items"]]
    assert statuses == ["accepted", "planning", "running", "verifying", "succeeded"]


def test_nodes_list_is_empty_initially(client: TestClient) -> None:
    r = client.get("/api/v1/nodes")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_openapi_schema_served(client: TestClient) -> None:
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["title"] == "SDN Controller"
    # Spot-check a couple of paths.
    assert "/api/v1/networks" in schema["paths"]
    assert "/api/v1/operations/{operation_id}" in schema["paths"]
