"""End-to-end IPAM flow through the FastAPI app + in-memory adapters.

Covers SDN-020 (subnet upsert with pool/reserved/dns) and SDN-021
(dynamic + reservation + release + idempotency + uniqueness).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def _make_network(client: TestClient, *, name: str = "tenant-a") -> str:
    r = client.post("/api/v1/networks", json={"name": name, "type": "vxlan", "vni": 10100})
    assert r.status_code == 202, r.text
    return str(r.json()["network"]["id"])


def _upsert_subnet(
    client: TestClient,
    network_id: str,
    *,
    cidr: str = "10.0.0.0/24",
    gateway: str | None = "10.0.0.1",
    pools: list[dict[str, str]] | None = None,
    reserved: list[dict[str, str]] | None = None,
    dns_servers: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"cidr": cidr, "gateway": gateway}
    if pools is not None:
        payload["allocation_pools"] = pools
    if reserved is not None:
        payload["reserved_ranges"] = reserved
    if dns_servers is not None:
        payload["dns_servers"] = dns_servers
    r = client.post(f"/api/v1/networks/{network_id}/subnet", json=payload)
    assert r.status_code == 202, r.text
    body: dict[str, Any] = r.json()
    return body


def test_upsert_subnet_attaches_to_network(client: TestClient) -> None:
    nid = _make_network(client)

    body = _upsert_subnet(
        client,
        nid,
        pools=[{"start": "10.0.0.100", "end": "10.0.0.200"}],
        dns_servers=["10.0.0.10"],
    )

    assert body["subnet"]["cidr"] == "10.0.0.0/24"
    listed = client.get("/api/v1/subnets").json()
    assert len(listed["items"]) == 1
    assert listed["items"][0]["network_id"] == nid
    assert listed["items"][0]["dns_servers"] == ["10.0.0.10"]
    assert listed["items"][0]["allocation_pools"] == [{"start": "10.0.0.100", "end": "10.0.0.200"}]


def test_upsert_with_overlapping_pools_returns_400(client: TestClient) -> None:
    nid = _make_network(client)

    r = client.post(
        f"/api/v1/networks/{nid}/subnet",
        json={
            "cidr": "10.0.0.0/24",
            "allocation_pools": [
                {"start": "10.0.0.10", "end": "10.0.0.50"},
                {"start": "10.0.0.40", "end": "10.0.0.60"},
            ],
        },
    )

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_dynamic_allocate_serves_consecutive_ips(client: TestClient) -> None:
    nid = _make_network(client)
    body = _upsert_subnet(client, nid)
    sid = body["subnet"]["id"]

    first = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={"kind": "dynamic", "owner": {"type": "vm-port", "id": "vm-1"}},
    )
    second = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={"kind": "dynamic", "owner": {"type": "vm-port", "id": "vm-2"}},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["ip_address"] == "10.0.0.2"
    assert second.json()["ip_address"] == "10.0.0.3"
    assert first.json()["kind"] == "dynamic"


def test_reservation_pins_specific_address(client: TestClient) -> None:
    nid = _make_network(client)
    body = _upsert_subnet(client, nid)
    sid = body["subnet"]["id"]

    r = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={
            "kind": "reservation",
            "ip_address": "10.0.0.42",
            "owner": {"type": "router-interface", "id": "rtr-1"},
            "label": "primary-gw",
        },
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ip_address"] == "10.0.0.42"
    assert body["kind"] == "reservation"
    assert body["label"] == "primary-gw"


def test_dynamic_skips_existing_reservations(client: TestClient) -> None:
    nid = _make_network(client)
    body = _upsert_subnet(client, nid)
    sid = body["subnet"]["id"]

    client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={
            "kind": "reservation",
            "ip_address": "10.0.0.2",
            "owner": {"type": "router-interface", "id": "rtr"},
        },
    )
    dyn = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={"kind": "dynamic", "owner": {"type": "vm-port", "id": "vm-1"}},
    )

    assert dyn.json()["ip_address"] == "10.0.0.3"


def test_double_reservation_returns_409(client: TestClient) -> None:
    nid = _make_network(client)
    body = _upsert_subnet(client, nid)
    sid = body["subnet"]["id"]

    client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={
            "kind": "reservation",
            "ip_address": "10.0.0.42",
            "owner": {"type": "router-interface", "id": "rtr-1"},
        },
    )
    second = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={
            "kind": "reservation",
            "ip_address": "10.0.0.42",
            "owner": {"type": "router-interface", "id": "rtr-2"},
        },
    )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


def test_reserve_gateway_rejected(client: TestClient) -> None:
    nid = _make_network(client)
    body = _upsert_subnet(client, nid)
    sid = body["subnet"]["id"]

    r = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={
            "kind": "reservation",
            "ip_address": "10.0.0.1",  # gateway
            "owner": {"type": "vm-port", "id": "vm-evil"},
        },
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_release_frees_ip_for_next_allocation(client: TestClient) -> None:
    nid = _make_network(client)
    body = _upsert_subnet(client, nid)
    sid = body["subnet"]["id"]

    first = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={"kind": "dynamic", "owner": {"type": "vm-port", "id": "vm-1"}},
    ).json()
    release = client.delete(f"/api/v1/allocations/{first['id']}")
    assert release.status_code == 204

    reuse = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={"kind": "dynamic", "owner": {"type": "vm-port", "id": "vm-2"}},
    ).json()
    assert reuse["ip_address"] == first["ip_address"]


def test_release_is_idempotent(client: TestClient) -> None:
    nid = _make_network(client)
    body = _upsert_subnet(client, nid)
    sid = body["subnet"]["id"]
    alloc = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={"kind": "dynamic", "owner": {"type": "vm-port", "id": "vm-1"}},
    ).json()

    assert client.delete(f"/api/v1/allocations/{alloc['id']}").status_code == 204
    assert client.delete(f"/api/v1/allocations/{alloc['id']}").status_code == 204


def test_list_allocations_for_unknown_subnet_returns_404(client: TestClient) -> None:
    r = client.get("/api/v1/subnets/sub_unknown/allocations")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_reservation_missing_ip_address_returns_400(client: TestClient) -> None:
    nid = _make_network(client)
    body = _upsert_subnet(client, nid)
    sid = body["subnet"]["id"]

    r = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={"kind": "reservation", "owner": {"type": "vm-port", "id": "vm-1"}},
    )

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_pool_exhaustion_returns_409(client: TestClient) -> None:
    nid = _make_network(client)
    body = _upsert_subnet(
        client,
        nid,
        pools=[{"start": "10.0.0.50", "end": "10.0.0.51"}],
    )
    sid = body["subnet"]["id"]
    client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={"kind": "dynamic", "owner": {"type": "vm-port", "id": "vm-1"}},
    )
    client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={"kind": "dynamic", "owner": {"type": "vm-port", "id": "vm-2"}},
    )

    third = client.post(
        f"/api/v1/subnets/{sid}/allocations",
        json={"kind": "dynamic", "owner": {"type": "vm-port", "id": "vm-3"}},
    )

    assert third.status_code == 409
    assert "no free addresses" in third.json()["error"]["message"]
