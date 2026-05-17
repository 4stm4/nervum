"""SDN-013 / SDN-014: VLAN access, VLAN trunk, VXLAN, external_ids — all
exercised end-to-end through the agent's ``/v1/network/apply`` endpoint
against the in-process ``FakeOvsdb`` backend.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from netos_agent.adapters.http_api import create_app
from netos_agent.app.config import Settings
from netos_agent.app.container import build_container


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        ovs_backend="fake",
        snapshots_dir=str(tmp_path / "snapshots"),
        log_level="WARNING",
        log_format="console",
    )
    container = build_container(settings)
    with TestClient(create_app(container)) as tc:
        yield tc


def _apply(
    client: TestClient,
    plan_id: str,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    r = client.post("/v1/network/apply", json={"plan_id": plan_id, "steps": steps})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def _bridge(state: dict[str, Any], name: str) -> dict[str, Any]:
    for b in state["bridges"]:
        if b["name"] == name:
            return cast("dict[str, Any]", b)
    raise AssertionError(f"bridge {name!r} not in state: {state['bridges']}")


def _port(bridge: dict[str, Any], name: str) -> dict[str, Any]:
    for p in bridge["ports"]:
        if p["name"] == name:
            return cast("dict[str, Any]", p)
    raise AssertionError(f"port {name!r} not in bridge {bridge['name']!r}")


# ---------------------------------------------------------------------------
# VLAN access port
# ---------------------------------------------------------------------------


def test_vlan_access_port_sets_tag(client: TestClient) -> None:
    _apply(
        client,
        "plan_vlan_access",
        [
            {"action": "ensure_bridge", "name": "br-int"},
            {
                "action": "ensure_port",
                "bridge": "br-int",
                "name": "vm-eth0",
                "type": "internal",
                "tag": 100,
            },
        ],
    )

    state = client.get("/v1/ovs/state").json()
    port = _port(_bridge(state, "br-int"), "vm-eth0")
    assert port["tag"] == 100
    assert port["trunks"] == []


# ---------------------------------------------------------------------------
# VLAN trunk port
# ---------------------------------------------------------------------------


def test_vlan_trunk_port_lists_allowed_vlans(client: TestClient) -> None:
    _apply(
        client,
        "plan_vlan_trunk",
        [
            {"action": "ensure_bridge", "name": "br-int"},
            {
                "action": "ensure_port",
                "bridge": "br-int",
                "name": "uplink0",
                "type": "system",
                "trunks": [10, 20, 30],
            },
        ],
    )

    state = client.get("/v1/ovs/state").json()
    port = _port(_bridge(state, "br-int"), "uplink0")
    assert port["tag"] is None
    assert sorted(port["trunks"]) == [10, 20, 30]


def test_vlan_id_out_of_range_rejected(client: TestClient) -> None:
    r = client.post(
        "/v1/network/apply",
        json={
            "plan_id": "plan_bad_vlan",
            "steps": [
                {"action": "ensure_bridge", "name": "br-int"},
                {
                    "action": "ensure_port",
                    "bridge": "br-int",
                    "name": "vm-eth0",
                    "type": "internal",
                    "tag": 5000,  # > 4094
                },
            ],
        },
    )

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "request_validation_error"


# ---------------------------------------------------------------------------
# VXLAN with all options
# ---------------------------------------------------------------------------


def test_vxlan_port_with_local_ip_dst_port_mtu(client: TestClient) -> None:
    _apply(
        client,
        "plan_vxlan",
        [
            {"action": "ensure_bridge", "name": "br-tun"},
            {
                "action": "ensure_vxlan_port",
                "bridge": "br-tun",
                "name": "vxlan-10100-n2",
                "vni": 10100,
                "remote_ip": "10.0.0.2",
                "local_ip": "10.0.0.1",
                "dst_port": 8472,
                "mtu": 1450,
            },
        ],
    )

    state = client.get("/v1/ovs/state").json()
    port = _port(_bridge(state, "br-tun"), "vxlan-10100-n2")
    iface = port["interfaces"][0]
    assert iface["type"] == "vxlan"
    assert iface["options"]["key"] == "10100"
    assert iface["options"]["remote_ip"] == "10.0.0.2"
    assert iface["options"]["local_ip"] == "10.0.0.1"
    assert iface["options"]["dst_port"] == "8472"
    assert iface["options"]["mtu_request"] == "1450"


def test_vxlan_idempotent(client: TestClient) -> None:
    plan = {
        "plan_id": "plan_vxlan_idem",
        "steps": [
            {"action": "ensure_bridge", "name": "br-tun"},
            {
                "action": "ensure_vxlan_port",
                "bridge": "br-tun",
                "name": "vxlan-1",
                "vni": 1,
                "remote_ip": "10.0.0.2",
            },
        ],
    }

    first = client.post("/v1/network/apply", json=plan).json()
    second = client.post("/v1/network/apply", json=plan).json()

    assert all(s["changed"] for s in first["steps"])
    assert all(s["changed"] is False for s in second["steps"])


# ---------------------------------------------------------------------------
# external_ids
# ---------------------------------------------------------------------------


def test_external_ids_round_trip_on_bridge_and_port(client: TestClient) -> None:
    _apply(
        client,
        "plan_xids",
        [
            {
                "action": "ensure_bridge",
                "name": "br-tenant",
                "external_ids": {"owner": "sdn-controller", "network_id": "net_42"},
            },
            {
                "action": "ensure_port",
                "bridge": "br-tenant",
                "name": "vm1-eth0",
                "type": "internal",
                "external_ids": {"port_id": "port_001"},
            },
        ],
    )

    state = client.get("/v1/ovs/state").json()
    bridge = _bridge(state, "br-tenant")
    port = _port(bridge, "vm1-eth0")
    assert bridge["external_ids"] == {"owner": "sdn-controller", "network_id": "net_42"}
    assert port["external_ids"] == {"port_id": "port_001"}


def test_external_ids_change_triggers_changed(client: TestClient) -> None:
    # First apply
    first = _apply(
        client,
        "plan_x1",
        [
            {
                "action": "ensure_bridge",
                "name": "br-tenant",
                "external_ids": {"owner": "controller-a"},
            }
        ],
    )
    assert first["steps"][0]["changed"] is True

    # Re-apply with different external_ids — must be ``changed``
    second = _apply(
        client,
        "plan_x2",
        [
            {
                "action": "ensure_bridge",
                "name": "br-tenant",
                "external_ids": {"owner": "controller-b"},
            }
        ],
    )
    assert second["steps"][0]["changed"] is True

    # Same external_ids → noop
    third = _apply(
        client,
        "plan_x3",
        [
            {
                "action": "ensure_bridge",
                "name": "br-tenant",
                "external_ids": {"owner": "controller-b"},
            }
        ],
    )
    assert third["steps"][0]["changed"] is False


def test_external_ids_survive_snapshot_restore(client: TestClient) -> None:
    _apply(
        client,
        "plan_pre",
        [
            {
                "action": "ensure_bridge",
                "name": "br-prod",
                "external_ids": {"owner": "sdn"},
            }
        ],
    )
    snap = client.post("/v1/ovs/snapshot", json={"label": "with-xids"}).json()
    pre_hash = client.get("/v1/ovs/state").json()["state_hash"]

    # Mutate (drop external_ids), then restore
    _apply(
        client,
        "plan_mut",
        [{"action": "ensure_bridge", "name": "br-prod", "external_ids": {}}],
    )
    assert client.get("/v1/ovs/state").json()["state_hash"] != pre_hash

    restored = client.post(f"/v1/ovs/restore/{snap['id']}").json()
    assert restored["ovs_state"]["state_hash"] == pre_hash
    state = client.get("/v1/ovs/state").json()
    assert _bridge(state, "br-prod")["external_ids"] == {"owner": "sdn"}
