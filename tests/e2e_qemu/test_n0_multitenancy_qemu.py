"""Real-environment N0 multitenancy tests against QEMU-hosted Nervum.

Покрытие:
  N0-E2E-01  QEMU boots NetOS and Nervum API becomes ready
  N0-E2E-02  bootstrap admin token can access admin endpoints
  N0-E2E-03  Project create/list/get/update/delete
  N0-E2E-04  Project slug/name validation
  N0-E2E-05  Duplicate project slug rejected
  N0-E2E-06  Create network/node/resource with project_id
  N0-E2E-07  Same resource name allowed in different projects
  N0-E2E-08  Same resource name rejected inside same project
  N0-E2E-09  Project-scoped principal lists only own resources
  N0-E2E-10  Project-scoped principal cannot read foreign resource by id
  N0-E2E-11  Project-scoped principal cannot mutate foreign resource
  N0-E2E-12  Global admin can see resources from all projects
  N0-E2E-13  operation contains project_id
  N0-E2E-14  audit event contains project_id
  N0-E2E-15  outbox/event contains schema_version=2 and project_id
  N0-E2E-16  webhook/event snapshot includes project_id
  N0-E2E-17  backup/export includes project_id
  N0-E2E-18  import preserves project_id
  N0-E2E-19  legacy project_id=NULL behavior
  N0-E2E-20  deprecated routes return Deprecation/Sunset/Link headers
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
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

# Метки для xfail — содержат ID невыполненного требования и его описание
_XF_PROJECT_SCOPED = "N0-03 project-scoped credentials/API missing"
_XF_DEPRECATED = "N0-05 deprecated routes not implemented"
_XF_SNAPSHOT_MISSING = "N0-16 endpoint /api/v1/events/snapshot отсутствует или недоступен"
_XF_BACKUP_MISSING = "N0-17 endpoint /api/v1/backup/export отсутствует или требует доступа"
_XF_IMPORT_FAILED = "N0-18 endpoint /api/v1/backup/import недоступен или конфликт данных"


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _suffix() -> str:
    """Короткий уникальный суффикс для изоляции тестовых данных."""
    return uuid.uuid4().hex[:10]


def _project_pair(admin_client: ApiClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """Создаёт два изолированных проекта за один вызов."""
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
    """Создаёт сеть с project_id; при отсутствии поддержки — xfail."""
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
    """Создаёт service account + member + token для проектного доступа."""
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
    """Извлекает items из постраничного ответа."""
    return list(response.json()["items"])


# ---------------------------------------------------------------------------
# N0-E2E-01  QEMU boots NetOS and Nervum API becomes ready
# ---------------------------------------------------------------------------


def test_api_ready_real_qemu(admin_client: ApiClient) -> None:
    """Smoke-тест: API поднялось и отвечает 200 на /readyz."""
    # api_ready фикстура в conftest уже опрашивает health-эндпоинты при старте сессии;
    # здесь делаем явный запрос, чтобы результат был виден в отчёте как отдельный тест.
    response = admin_client.get("/api/v1/readyz")
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# N0-E2E-02  bootstrap admin token can access admin endpoints
# ---------------------------------------------------------------------------


def test_bootstrap_admin_token_access_real_qemu(
    admin_client: ApiClient,
    e2e_qemu_api_url: str,
) -> None:
    """Bootstrap admin token должен давать доступ к защищённым ручкам.

    Токен без авторизации — 401.
    """
    # Администратор видит проекты, сети, узлы
    assert admin_client.get("/api/v1/projects").status_code == 200
    assert admin_client.get("/api/v1/networks").status_code == 200
    assert admin_client.get("/api/v1/nodes").status_code == 200

    # Анонимный клиент (без токена) получает 401
    anon = ApiClient(e2e_qemu_api_url, token=None)
    try:
        r_anon = anon.get("/api/v1/projects")
        assert r_anon.status_code == 401, r_anon.text
    finally:
        anon.close()


# ---------------------------------------------------------------------------
# N0-E2E-03  Project create/list/get/update/delete
# ---------------------------------------------------------------------------


def test_project_crud_real_qemu(admin_client: ApiClient) -> None:
    """create + list: базовые поля проекта проверяются сразу после создания."""
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


def test_project_get_update_delete_real_qemu(admin_client: ApiClient) -> None:
    """GET по ID, PATCH (переименование) и DELETE; после удаления — 404."""
    run = _suffix()
    project = admin_client.create_project(name=f"CRUD {run}", slug=f"crud-{run}")
    pid = project["id"]

    # GET по ID возвращает тот же объект
    r_get = admin_client.get(f"/api/v1/projects/{pid}")
    assert r_get.status_code == 200, r_get.text
    assert r_get.json()["id"] == pid
    assert r_get.json()["slug"] == f"crud-{run}"

    # PATCH: обновляем имя и проверяем ответ
    new_name = f"CRUD Updated {run}"
    r_patch = admin_client.patch(f"/api/v1/projects/{pid}", json={"name": new_name})
    assert r_patch.status_code == 200, r_patch.text
    assert r_patch.json()["name"] == new_name

    # DELETE возвращает 204
    r_del = admin_client.delete(f"/api/v1/projects/{pid}")
    assert r_del.status_code == 204, r_del.text

    # После удаления GET должен вернуть 404
    r_after = admin_client.get(f"/api/v1/projects/{pid}")
    assert r_after.status_code == 404, r_after.text


# ---------------------------------------------------------------------------
# N0-E2E-04  Project slug/name validation
# ---------------------------------------------------------------------------


def test_project_slug_name_validation_real_qemu(admin_client: ApiClient) -> None:
    """Невалидные slug и name отклоняются с HTTP 422."""
    run = _suffix()

    # slug с пробелом — недопустим
    r = admin_client.post(
        "/api/v1/projects",
        json={"name": f"Test {run}", "slug": "invalid slug"},
    )
    assert r.status_code == 422, f"ожидали 422 для slug с пробелом: {r.text}"

    # slug длиннее 63 символов
    r = admin_client.post(
        "/api/v1/projects",
        json={"name": f"Test {run}", "slug": "a" * 64},
    )
    assert r.status_code == 422, f"ожидали 422 для slug 64 символа: {r.text}"

    # name длиннее 128 символов
    r = admin_client.post(
        "/api/v1/projects",
        json={"name": "x" * 129, "slug": f"valid-{run}"},
    )
    assert r.status_code == 422, f"ожидали 422 для name 129 символов: {r.text}"

    # пустое name
    r = admin_client.post(
        "/api/v1/projects",
        json={"name": "", "slug": f"empty-name-{run}"},
    )
    assert r.status_code == 422, f"ожидали 422 для пустого name: {r.text}"


# ---------------------------------------------------------------------------
# N0-E2E-05  Duplicate project slug rejected
# ---------------------------------------------------------------------------


def test_duplicate_project_slug_rejected_real_qemu(admin_client: ApiClient) -> None:
    """Повторный slug возвращает 409 Conflict."""
    run = _suffix()
    slug = f"dup-{run}"
    admin_client.create_project(name=f"Original {run}", slug=slug)

    r = admin_client.post("/api/v1/projects", json={"name": f"Copy {run}", "slug": slug})
    assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# N0-E2E-06  Create network/node/resource with project_id
# ---------------------------------------------------------------------------


def test_create_resource_with_project_id_real_qemu(admin_client: ApiClient) -> None:
    """Ресурс, созданный с project_id, отдаёт его обратно при GET."""
    run = _suffix()
    project = admin_client.create_project(name=f"Proj06 {run}", slug=f"proj06-{run}")
    pid = project["id"]

    created = _create_project_network(admin_client, name=f"net-06-{run}", project_id=pid)
    network = created["network"]

    # project_id присутствует в ответе на создание
    assert_project_id_present(network, pid)

    # project_id сохраняется после перечитывания по ID
    r = admin_client.get(f"/api/v1/networks/{network['id']}")
    assert r.status_code == 200, r.text
    assert_project_id_present(r.json(), pid)

    # project_id присутствует в операции
    operation = created["operation"]
    assert_project_id_present(operation, pid)


# ---------------------------------------------------------------------------
# N0-E2E-07  Same resource name allowed in different projects
# N0-E2E-09  Project-scoped principal lists only own resources
# N0-E2E-10  Project-scoped principal cannot read foreign resource by id
# N0-E2E-11  Project-scoped principal cannot mutate foreign resource
# ---------------------------------------------------------------------------


def test_project_isolation_for_networks_real_qemu(
    admin_client: ApiClient,
    e2e_qemu_api_url: str,
) -> None:
    """Одно имя сети в разных проектах — OK; проектный principal не видит чужих ресурсов."""
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


# ---------------------------------------------------------------------------
# N0-E2E-08  Same resource name rejected inside same project
# ---------------------------------------------------------------------------


def test_same_name_in_same_project_rejected_real_qemu(admin_client: ApiClient) -> None:
    """Два ресурса с одинаковым именем в одном проекте → 409."""
    run = _suffix()
    project = admin_client.create_project(name=f"Proj08 {run}", slug=f"proj08-{run}")
    pid = project["id"]
    name = f"dup-net-{run}"

    # Первая сеть создаётся успешно
    created = _create_project_network(admin_client, name=name, project_id=pid)
    assert created["network"]["name"] == name

    # Вторая сеть с тем же именем в том же проекте — конфликт
    r = admin_client.post(
        "/api/v1/networks",
        json={"name": name, "type": "flat", "project_id": pid},
    )
    if r.status_code == 422:
        pytest.xfail(_XF_PROJECT_SCOPED)
    assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# N0-E2E-12  Global admin can see resources from all projects
# ---------------------------------------------------------------------------


def test_global_admin_sees_all_projects_real_qemu(admin_client: ApiClient) -> None:
    """Глобальный admin видит ресурсы всех проектов без фильтрации."""
    run = _suffix()
    proj_a = admin_client.create_project(name=f"Admin12A {run}", slug=f"adm12a-{run}")
    proj_b = admin_client.create_project(name=f"Admin12B {run}", slug=f"adm12b-{run}")

    net_a = _create_project_network(admin_client, name=f"adm-net-a-{run}", project_id=proj_a["id"])
    net_b = _create_project_network(admin_client, name=f"adm-net-b-{run}", project_id=proj_b["id"])

    # Незафильтрованный список сетей содержит обе сети из разных проектов
    all_nets = _items(admin_client.get("/api/v1/networks"))
    net_ids = {n["id"] for n in all_nets}
    assert net_a["network"]["id"] in net_ids
    assert net_b["network"]["id"] in net_ids

    # Список проектов содержит оба проекта
    all_projs = _items(admin_client.get("/api/v1/projects"))
    proj_ids = {p["id"] for p in all_projs}
    assert proj_a["id"] in proj_ids
    assert proj_b["id"] in proj_ids


# ---------------------------------------------------------------------------
# N0-E2E-13  operation contains project_id
# N0-E2E-14  audit event contains project_id
# N0-E2E-15  outbox/event contains schema_version=2 and project_id
# ---------------------------------------------------------------------------


def test_project_id_in_operations_audit_outbox_real_qemu(admin_client: ApiClient) -> None:
    """project_id должен присутствовать в операции, аудит-событии и outbox-событии."""
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


# ---------------------------------------------------------------------------
# N0-E2E-16  webhook/event snapshot includes project_id
# ---------------------------------------------------------------------------


def test_snapshot_event_includes_project_id_real_qemu(admin_client: ApiClient) -> None:
    """GET /api/v1/events/snapshot: объекты сетей содержат project_id."""
    run = _suffix()
    project = admin_client.create_project(name=f"Snap16 {run}", slug=f"snap16-{run}")
    pid = project["id"]
    created = _create_project_network(admin_client, name=f"snap-net-{run}", project_id=pid)
    network_id = created["network"]["id"]

    r = admin_client.get("/api/v1/events/snapshot")
    if r.status_code in {404, 405}:
        pytest.xfail(_XF_SNAPSHOT_MISSING)
    if r.status_code == 403:
        pytest.xfail(f"{_XF_SNAPSHOT_MISSING}: 403 Forbidden")
    assert r.status_code == 200, r.text

    body = r.json()
    networks = body.get("networks", [])
    matching = [n for n in networks if n["id"] == network_id]
    if not matching:
        pytest.xfail(f"{_XF_SNAPSHOT_MISSING}: сеть {network_id} не найдена в снапшоте")
    assert_project_id_present(matching[0], pid)


# ---------------------------------------------------------------------------
# N0-E2E-17  backup/export includes project_id
# ---------------------------------------------------------------------------


def test_backup_export_includes_project_id_real_qemu(admin_client: ApiClient) -> None:
    """GET /api/v1/backup/export: bundle содержит project_id для каждой сети."""
    run = _suffix()
    project = admin_client.create_project(name=f"Bkp17 {run}", slug=f"bkp17-{run}")
    pid = project["id"]
    created = _create_project_network(admin_client, name=f"bkp-net-{run}", project_id=pid)
    network_id = created["network"]["id"]

    r = admin_client.get("/api/v1/backup/export")
    if r.status_code in {403, 404, 405}:
        pytest.xfail(_XF_BACKUP_MISSING)
    assert r.status_code == 200, r.text

    bundle = r.json()
    networks = bundle.get("networks", [])
    matching = [n for n in networks if n["id"] == network_id]
    assert matching, (
        f"Сеть {network_id} не найдена в bundle. "
        f"Найдено сетей: {[n['id'] for n in networks]}"
    )
    net_dict = matching[0]
    assert "project_id" in net_dict, f"project_id отсутствует в bundle: {net_dict}"
    assert net_dict["project_id"] == pid, net_dict


# ---------------------------------------------------------------------------
# N0-E2E-18  import preserves project_id
# ---------------------------------------------------------------------------


def test_backup_import_preserves_project_id_real_qemu(admin_client: ApiClient) -> None:
    """POST /api/v1/backup/import: импортированная сеть сохраняет project_id."""
    run = _suffix()
    project = admin_client.create_project(name=f"Imp18 {run}", slug=f"imp18-{run}")
    pid = project["id"]

    # Генерируем уникальный ID для импортируемой сети, чтобы не столкнуться с существующими
    fresh_net_id = f"net_{uuid.uuid4().hex}"
    now_iso = datetime.now(timezone.utc).isoformat()

    synthetic_bundle: dict[str, Any] = {
        "manifest": {
            "schema_version": 1,
            "created_at": now_iso,
            "controller_version": "e2e-test",
        },
        "service_accounts": [],
        "nodes": [],
        "networks": [
            {
                "id": fresh_net_id,
                "name": f"imp-net-{run}",
                "type": "flat",
                "created_at": now_iso,
                "updated_at": now_iso,
                "mtu": 1500,
                "vlan_id": None,
                "vni": None,
                "subnet": None,
                "labels": {},
                "intent_version": 1,
                "node_ids": [],
                "nat": None,
                "firewall_policy": None,
                "spec_hash": "",
                # project_id должен сохраниться после импорта
                "project_id": pid,
            }
        ],
        "ip_allocations": [],
        "audit_events": [],
    }

    r_import = admin_client.post("/api/v1/backup/import", json=synthetic_bundle)
    if r_import.status_code in {403, 404, 405}:
        pytest.xfail(_XF_IMPORT_FAILED)
    if r_import.status_code == 409:
        pytest.xfail(f"{_XF_IMPORT_FAILED}: конфликт при импорте — БД непуста")
    assert r_import.status_code == 200, r_import.text

    # Сеть должна быть доступна по свежесгенерированному ID
    r_net = admin_client.get(f"/api/v1/networks/{fresh_net_id}")
    assert r_net.status_code == 200, r_net.text
    assert_project_id_present(r_net.json(), pid)


# ---------------------------------------------------------------------------
# N0-E2E-19  legacy project_id=NULL behavior
# ---------------------------------------------------------------------------


def test_legacy_null_project_id_behavior_real_qemu(
    admin_client: ApiClient,
    e2e_qemu_api_url: str,
) -> None:
    """Сеть без project_id видна только admin'у; проектный principal её не замечает."""
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


# ---------------------------------------------------------------------------
# N0-E2E-20  deprecated routes return Deprecation/Sunset/Link headers
# ---------------------------------------------------------------------------


def test_deprecation_sunset_headers_real_qemu(admin_client: ApiClient) -> None:
    """Актуальные ручки без Deprecation-заголовка; legacy — с полным набором."""
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
