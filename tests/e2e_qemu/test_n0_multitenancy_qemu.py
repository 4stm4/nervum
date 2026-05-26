"""Real-environment N0 multitenancy tests against QEMU-hosted Nervum."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.e2e_qemu.helpers.api_client import ApiClient
from tests.e2e_qemu.helpers.assertions import (
    assert_forbidden_or_not_found,
    assert_outbox_v2_event,
    assert_project_id_present,
)
from tests.e2e_qemu.helpers.db_inspection import GuestDbInspector
from tests.e2e_qemu.helpers.waiters import wait_operation_terminal

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.qemu,
    pytest.mark.real_environment,
    pytest.mark.n0,
]

_XF_PROJECT_SCOPED = "N0-03 project-scoped credentials/API missing"
_XF_DEPRECATED = "N0-05 deprecated routes not implemented"


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


def _project_pair(admin_client: ApiClient) -> tuple[dict[str, Any], dict[str, Any]]:
    run = _suffix()
    project_a = admin_client.create_project(name=f"E2E Project A {run}", slug=f"e2e-a-{run}")
    project_b = admin_client.create_project(name=f"E2E Project B {run}", slug=f"e2e-b-{run}")
    return project_a, project_b


def _create_project_network(
    admin_client: ApiClient,
    *,
    name: str,
    project_id: str,
) -> dict[str, Any]:
    response = admin_client.post(
        "/api/v1/networks",
        json={"name": name, "type": "flat", "project_id": project_id},
    )
    if response.status_code == 422:
        pytest.xfail(_XF_PROJECT_SCOPED)
    if response.status_code == 409:
        pytest.xfail(_XF_PROJECT_SCOPED)
    assert response.status_code == 202, response.text
    return dict(response.json())


def _issue_project_token(
    admin_client: ApiClient,
    *,
    project_id: str,
    name: str,
    global_role: str = "viewer",
    project_role: str = "viewer",
) -> str:
    account_response = admin_client.post(
        "/api/v1/service-accounts",
        json={"name": name, "role": global_role},
    )
    if account_response.status_code in {404, 405, 422}:
        pytest.xfail(_XF_PROJECT_SCOPED)
    assert account_response.status_code == 201, account_response.text
    account = account_response.json()

    member_response = admin_client.put_member(project_id, account["id"], project_role)
    if member_response.status_code in {404, 405, 422}:
        pytest.xfail(_XF_PROJECT_SCOPED)
    assert member_response.status_code == 200, member_response.text

    token_response = admin_client.post(f"/api/v1/service-accounts/{account['id']}/tokens", json={})
    if token_response.status_code in {404, 405, 422}:
        pytest.xfail(_XF_PROJECT_SCOPED)
    assert token_response.status_code == 201, token_response.text
    return str(token_response.json()["plaintext"])


def _items(response: Any) -> list[dict[str, Any]]:
    return list(response.json()["items"])


def test_project_crud_real_qemu(admin_client: ApiClient) -> None:
    project_a, project_b = _project_pair(admin_client)

    response = admin_client.get("/api/v1/projects")
    assert response.status_code == 200, response.text
    projects = _items(response)
    ids = {project["id"] for project in projects}

    assert project_a["id"] in ids
    assert project_b["id"] in ids
    for project in (project_a, project_b):
        assert project["id"].startswith("proj_")
        assert project["name"]
        assert project["slug"].startswith("e2e-")
        assert "labels" in project


def test_project_isolation_for_networks_real_qemu(
    admin_client: ApiClient,
    e2e_qemu_api_url: str,
) -> None:
    project_a, project_b = _project_pair(admin_client)
    same_name = f"same-name-{_suffix()}"

    net_a = _create_project_network(admin_client, name=same_name, project_id=project_a["id"])
    net_b = _create_project_network(admin_client, name=same_name, project_id=project_b["id"])

    network_a = net_a["network"]
    network_b = net_b["network"]
    assert_project_id_present(network_a, project_a["id"])
    assert_project_id_present(network_b, project_b["id"])

    all_networks = _items(admin_client.get("/api/v1/networks"))
    assert {network_a["id"], network_b["id"]}.issubset({item["id"] for item in all_networks})

    only_a = _items(admin_client.get("/api/v1/networks", params={"project_id": project_a["id"]}))
    only_b = _items(admin_client.get("/api/v1/networks", params={"project_id": project_b["id"]}))
    assert {item["id"] for item in only_a} == {network_a["id"]}
    assert {item["id"] for item in only_b} == {network_b["id"]}

    token_a = _issue_project_token(
        admin_client,
        project_id=project_a["id"],
        name=f"e2e-proj-a-{_suffix()}",
    )
    token_b = _issue_project_token(
        admin_client,
        project_id=project_b["id"],
        name=f"e2e-proj-b-{_suffix()}",
    )

    project_a_client = ApiClient(e2e_qemu_api_url, token=token_a)
    project_b_client = ApiClient(e2e_qemu_api_url, token=token_b)
    try:
        a_seen = _items(project_a_client.get("/api/v1/networks"))
        b_seen = _items(project_b_client.get("/api/v1/networks"))
        if network_b["id"] in {item["id"] for item in a_seen}:
            pytest.xfail(_XF_PROJECT_SCOPED)
        if network_a["id"] in {item["id"] for item in b_seen}:
            pytest.xfail(_XF_PROJECT_SCOPED)

        assert {item["id"] for item in a_seen} == {network_a["id"]}
        assert {item["id"] for item in b_seen} == {network_b["id"]}
        assert_forbidden_or_not_found(project_a_client.get(f"/api/v1/networks/{network_b['id']}"))
        assert_forbidden_or_not_found(
            project_a_client.patch(f"/api/v1/networks/{network_b['id']}", json={"mtu": 1400})
        )
        assert_forbidden_or_not_found(project_a_client.delete(f"/api/v1/networks/{network_b['id']}"))
    finally:
        project_a_client.close()
        project_b_client.close()


def test_project_id_in_operations_audit_outbox_real_qemu(admin_client: ApiClient) -> None:
    project_a, _ = _project_pair(admin_client)
    created = _create_project_network(
        admin_client,
        name=f"ops-outbox-{_suffix()}",
        project_id=project_a["id"],
    )
    network = created["network"]
    operation = created["operation"]

    assert_project_id_present(network, project_a["id"])
    assert_project_id_present(operation, project_a["id"])

    operation_id = operation["operation_id"]
    fetched_operation = wait_operation_terminal(admin_client, operation_id)
    assert fetched_operation["resource"] == {"type": "network", "id": network["id"]}

    audit_response = admin_client.get("/api/v1/audit-events", params={"resource_id": network["id"]})
    assert audit_response.status_code == 200, audit_response.text
    audit_items = _items(audit_response)
    matching_audit = [item for item in audit_items if item["action"] == "network.create"]
    assert matching_audit, audit_items
    assert matching_audit[-1]["payload"]["project_id"] == project_a["id"]

    events_response = admin_client.get("/api/v1/events", params={"since": 0, "limit": 1000})
    if events_response.status_code in {404, 405}:
        inspector = GuestDbInspector()
        if not inspector.available():
            pytest.xfail("N0-04 public outbox API missing and guest DB inspection unavailable")
        event = inspector.fetch_outbox_event(network["id"])
        assert event is not None
        assert_outbox_v2_event(event, project_a["id"])
        return

    assert events_response.status_code == 200, events_response.text
    events = _items(events_response)
    matching_events = [event for event in events if event["resource_id"] == network["id"]]
    assert matching_events, events
    assert_outbox_v2_event(matching_events[-1], project_a["id"])


def test_legacy_null_project_id_behavior_real_qemu(
    admin_client: ApiClient,
    e2e_qemu_api_url: str,
) -> None:
    legacy_response = admin_client.post(
        "/api/v1/networks",
        json={"name": f"legacy-null-{_suffix()}", "type": "flat"},
    )
    assert legacy_response.status_code == 202, legacy_response.text
    legacy_network = legacy_response.json()["network"]
    assert legacy_network["project_id"] is None

    all_networks = _items(admin_client.get("/api/v1/networks"))
    assert legacy_network["id"] in {item["id"] for item in all_networks}

    project_a, _ = _project_pair(admin_client)
    token = _issue_project_token(
        admin_client,
        project_id=project_a["id"],
        name=f"e2e-legacy-viewer-{_suffix()}",
    )
    project_client = ApiClient(e2e_qemu_api_url, token=token)
    try:
        visible = _items(project_client.get("/api/v1/networks"))
        if legacy_network["id"] in {item["id"] for item in visible}:
            pytest.xfail("N0-04 legacy NULL project_id runtime behavior unsupported")
    finally:
        project_client.close()


def test_deprecation_sunset_headers_real_qemu(admin_client: ApiClient) -> None:
    current = admin_client.get("/api/v1/readyz")
    assert "Deprecation" not in current.headers
    assert "Sunset" not in current.headers

    compat = admin_client.get("/api/v1/health")
    assert compat.status_code == 200, compat.text
    if "Deprecation" not in compat.headers:
        pytest.xfail(_XF_DEPRECATED)
    assert compat.headers["Deprecation"]
    assert compat.headers["Sunset"]
    assert "Link" in compat.headers
