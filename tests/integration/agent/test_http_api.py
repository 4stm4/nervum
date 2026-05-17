"""End-to-end agent flows through FastAPI's ``TestClient``.

The fixture builds the production ``Container`` with ``ovs_backend="fake"``
and a temp-dir snapshot store, so this exercises real wiring all the way
through the app factory, lifespan, dependency injection, and Pydantic IO.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

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
    app = create_app(container)
    with TestClient(app) as tc:
        yield tc


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_reports_ok_against_fake_backend(client: TestClient) -> None:
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ovs_version"]  # fake gives a version


def test_node_info_exposes_hostname(client: TestClient) -> None:
    r = client.get("/v1/node/info")
    assert r.status_code == 200
    assert r.json()["hostname"]


def test_apply_plan_creates_bridge_and_port(client: TestClient) -> None:
    plan = {
        "plan_id": "plan_int_1",
        "steps": [
            {"action": "ensure_bridge", "name": "br-int"},
            {
                "action": "ensure_port",
                "bridge": "br-int",
                "name": "patch-tun",
                "type": "patch",
            },
        ],
    }

    r = client.post("/v1/network/apply", json=plan)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert [s["action"] for s in body["steps"]] == ["ensure_bridge", "ensure_port"]
    assert all(s["changed"] for s in body["steps"])

    # State endpoint sees the change.
    state = client.get("/v1/ovs/state").json()
    assert [b["name"] for b in state["bridges"]] == ["br-int"]


def test_apply_plan_is_idempotent(client: TestClient) -> None:
    plan = {
        "plan_id": "plan_int_2",
        "steps": [{"action": "ensure_bridge", "name": "br-2"}],
    }
    first = client.post("/v1/network/apply", json=plan).json()
    second = client.post("/v1/network/apply", json=plan).json()

    assert first["steps"][0]["changed"] is True
    assert second["steps"][0]["changed"] is False
    assert second["ok"] is True


def test_apply_plan_invalid_step_returns_422(client: TestClient) -> None:
    r = client.post(
        "/v1/network/apply",
        json={
            "plan_id": "plan_bad",
            "steps": [{"action": "nope"}],
        },
    )

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "request_validation_error"


def test_apply_plan_failed_step_still_returns_structured_result(client: TestClient) -> None:
    r = client.post(
        "/v1/network/apply",
        json={
            "plan_id": "plan_partial",
            "steps": [
                {"action": "ensure_port", "bridge": "missing", "name": "p1"},
                {"action": "ensure_bridge", "name": "br-x"},
            ],
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["steps"][0]["ok"] is False
    assert body["steps"][0]["details"]["code"] == "not_found"
    assert body["steps"][1]["ok"] is True


def test_snapshot_and_restore_round_trip(client: TestClient) -> None:
    client.post(
        "/v1/network/apply",
        json={
            "plan_id": "plan_pre",
            "steps": [
                {"action": "ensure_bridge", "name": "br-stable"},
                {"action": "ensure_port", "bridge": "br-stable", "name": "p1"},
            ],
        },
    )

    snap = client.post("/v1/ovs/snapshot", json={"label": "pre-test"})
    assert snap.status_code == 201, snap.text
    snapshot_id = snap.json()["id"]
    pre_hash = client.get("/v1/ovs/state").json()["state_hash"]

    # Mutate state, then restore.
    client.post(
        "/v1/network/apply",
        json={
            "plan_id": "plan_mutate",
            "steps": [{"action": "ensure_bridge", "name": "br-extra"}],
        },
    )
    assert client.get("/v1/ovs/state").json()["state_hash"] != pre_hash

    restored = client.post(f"/v1/ovs/restore/{snapshot_id}")
    assert restored.status_code == 200, restored.text
    assert restored.json()["ovs_state"]["state_hash"] == pre_hash


def test_restore_unknown_snapshot_returns_404(client: TestClient) -> None:
    r = client.post("/v1/ovs/restore/snap_missing")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_system_stats_returns_uptime(client: TestClient) -> None:
    r = client.get("/v1/system/stats")

    assert r.status_code == 200
    assert r.json()["uptime_seconds"] is not None
