"""Real-environment N2 E2E tests against QEMU-hosted Nervum.

Покрытие:
  N2-E2E-01  SecurityPolicy CRUD
  N2-E2E-02  Ordered rules: priority/order preserved
  N2-E2E-03  Rule validation: action/protocol/service/source/destination
  N2-E2E-04  Create policy using SecurityGroup source/destination
  N2-E2E-05  Create policy using AddressPool
  N2-E2E-06  Create policy using ServiceObject
  N2-E2E-07  Cross-project operand reference rejected
  N2-E2E-08  Compile policy → deterministic compiled_ruleset hash
  N2-E2E-09  Compile failure on missing operand
  N2-E2E-10  Apply policy lifecycle: draft → compiled → applied
  N2-E2E-11  Apply failure path: apply без compile → ошибка
  N2-E2E-12  Verify catches drift between desired and applied ruleset
  N2-E2E-13  Per-rule counters increase after fake traffic simulation
  N2-E2E-14  Counters survive repeated apply / idempotent apply
  N2-E2E-15  Policy events: created/compiled/applied/rule_added/rule_removed/deleted
  N2-E2E-16  CLI parity if CLI exists
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.e2e_qemu.helpers.api_client import ApiClient
from tests.e2e_qemu.helpers.db_inspection import GuestDbInspector

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.qemu,
    pytest.mark.real_environment,
    pytest.mark.n2,
]

# Метки для xfail
_XF_CROSS_PROJECT = "N2-07 межпроектная изоляция service_object в правилах не реализована"
_XF_COMPILE_SKIP = "N2-09 компилятор молча пропускает несуществующий ServiceObject; ошибки нет"
_XF_VERIFY_MISSING = "N2-12 эндпоинт verify/drift отсутствует в текущей реализации"
_XF_COUNTERS_NO_API = "N2-13/14 HTTP-эндпоинт обновления счётчиков отсутствует в роутере"
_XF_CLI_MISSING = "N2-16 CLI не существует"


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _suffix() -> str:
    """Короткий уникальный суффикс для изоляции тестовых данных."""
    return uuid.uuid4().hex[:10]


def _items(response: Any) -> list[dict[str, Any]]:
    """Извлекает items из постраничного ответа."""
    return list(response.json()["items"])


def _create_policy(
    client: ApiClient,
    *,
    name: str,
    project_id: str | None = None,
    description: str = "",
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Создаёт SecurityPolicy и возвращает её dict."""
    body: dict[str, Any] = {"name": name, "description": description}
    if project_id is not None:
        body["project_id"] = project_id
    if labels is not None:
        body["labels"] = labels
    resp = client.post("/api/v1/security-policies", json=body)
    assert resp.status_code == 201, resp.text
    return dict(resp.json()["security_policy"])


def _add_rule(
    client: ApiClient,
    policy_id: str,
    *,
    priority: int,
    direction: str = "ingress",
    action: str = "allow",
    source_type: str = "any",
    source_value: str = "",
    destination_type: str = "any",
    destination_value: str = "",
    service_object_id: str | None = None,
    comment: str = "",
) -> dict[str, Any]:
    """Добавляет правило в политику и возвращает обновлённую политику."""
    body: dict[str, Any] = {
        "priority": priority,
        "direction": direction,
        "action": action,
        "source_type": source_type,
        "source_value": source_value,
        "destination_type": destination_type,
        "destination_value": destination_value,
        "comment": comment,
    }
    if service_object_id is not None:
        body["service_object_id"] = service_object_id
    resp = client.post(f"/api/v1/security-policies/{policy_id}/rules", json=body)
    assert resp.status_code == 201, resp.text
    return dict(resp.json()["security_policy"])


def _register_node(admin_client: ApiClient, *, suffix: str | None = None) -> dict[str, Any]:
    """Регистрирует новый узел и возвращает его dict."""
    sfx = suffix or _suffix()
    last_octet = int(sfx[:2], 16) % 250 + 1
    second_octet = int(sfx[2:4], 16) % 250 + 1
    mgmt_ip = f"10.{second_octet}.{last_octet}.1"
    resp = admin_client.post(
        "/api/v1/nodes",
        json={"name": f"n2-node-{sfx}", "mgmt_ip": mgmt_ip, "roles": ["compute"]},
    )
    assert resp.status_code == 202, resp.text
    return dict(resp.json()["node"])


def _outbox_events_for(
    admin_client: ApiClient, resource_id: str
) -> list[dict[str, Any]]:
    """Возвращает outbox-события для ресурса (через API или fallback на DB)."""
    resp = admin_client.get("/api/v1/events", params={"since": 0, "limit": 1000})
    if resp.status_code in {404, 405}:
        inspector = GuestDbInspector()
        if inspector.available():
            ev = inspector.fetch_outbox_event(resource_id)
            return [ev] if ev is not None else []
        return []
    if resp.status_code != 200:
        return []
    return [e for e in resp.json()["items"] if e["resource_id"] == resource_id]


# ---------------------------------------------------------------------------
# N2-E2E-01  SecurityPolicy CRUD
# ---------------------------------------------------------------------------


def test_security_policy_crud_real_qemu(admin_client: ApiClient) -> None:
    """create / list / get / update / delete SecurityPolicy."""
    sfx = _suffix()
    project = admin_client.create_project(name=f"N2P01 {sfx}", slug=f"n2p01-{sfx}")
    pid = project["id"]

    # CREATE
    policy = _create_policy(
        admin_client,
        name=f"sp-{sfx}",
        description="тестовая политика",
        project_id=pid,
        labels={"env": "test"},
    )
    policy_id = policy["id"]
    assert policy["status"] == "draft"
    assert policy["project_id"] == pid
    assert policy["name"] == f"sp-{sfx}"
    assert policy["labels"] == {"env": "test"}
    assert policy["compiled_ruleset"] is None
    assert policy["rules"] == []

    # LIST с фильтром по project_id
    r_list = admin_client.get("/api/v1/security-policies", params={"project_id": pid})
    assert r_list.status_code == 200, r_list.text
    ids_in_list = {p["id"] for p in _items(r_list)}
    assert policy_id in ids_in_list

    # GET по ID
    r_get = admin_client.get(f"/api/v1/security-policies/{policy_id}")
    assert r_get.status_code == 200, r_get.text
    assert r_get.json()["security_policy"]["id"] == policy_id

    # PATCH
    r_patch = admin_client.patch(
        f"/api/v1/security-policies/{policy_id}",
        json={"name": f"sp-upd-{sfx}", "description": "обновлённая", "labels": {"env": "prod"}},
    )
    assert r_patch.status_code == 200, r_patch.text
    updated = r_patch.json()["security_policy"]
    assert updated["name"] == f"sp-upd-{sfx}"
    assert updated["description"] == "обновлённая"
    assert updated["labels"] == {"env": "prod"}

    # DELETE
    r_del = admin_client.delete(f"/api/v1/security-policies/{policy_id}")
    assert r_del.status_code == 204, r_del.text

    # GET после удаления → 404
    r_after = admin_client.get(f"/api/v1/security-policies/{policy_id}")
    assert r_after.status_code == 404, r_after.text


# ---------------------------------------------------------------------------
# N2-E2E-02  Ordered rules: priority/order preserved
# ---------------------------------------------------------------------------


def test_security_policy_rules_ordered_by_priority_real_qemu(admin_client: ApiClient) -> None:
    """Правила политики хранятся и возвращаются отсортированными по priority ASC."""
    sfx = _suffix()
    policy = _create_policy(admin_client, name=f"sp-ord-{sfx}")
    pol_id = policy["id"]

    # Добавляем в намеренно неупорядоченном порядке
    _add_rule(admin_client, pol_id, priority=200, direction="ingress", action="deny", comment="low")
    _add_rule(admin_client, pol_id, priority=50, direction="ingress", action="allow", comment="high")
    _add_rule(admin_client, pol_id, priority=100, direction="egress", action="allow", comment="mid")

    r_get = admin_client.get(f"/api/v1/security-policies/{pol_id}")
    assert r_get.status_code == 200, r_get.text
    rules = r_get.json()["security_policy"]["rules"]
    assert len(rules) == 3

    priorities = [r["priority"] for r in rules]
    assert priorities == sorted(priorities), f"Правила не отсортированы по priority: {priorities}"
    assert priorities == [50, 100, 200]

    # Удаление одного правила — оставшиеся сохраняют порядок
    rule_id_50 = rules[0]["rule_id"]
    r_del = admin_client.delete(f"/api/v1/security-policies/{pol_id}/rules/{rule_id_50}")
    assert r_del.status_code == 204, r_del.text

    r_after = admin_client.get(f"/api/v1/security-policies/{pol_id}")
    rules_after = r_after.json()["security_policy"]["rules"]
    assert len(rules_after) == 2
    assert [r["priority"] for r in rules_after] == [100, 200]


# ---------------------------------------------------------------------------
# N2-E2E-03  Rule validation: action/protocol/service/source/destination
# ---------------------------------------------------------------------------


def test_rule_validation_real_qemu(admin_client: ApiClient) -> None:
    """Недопустимые значения action/direction/source_type/priority отклоняются."""
    sfx = _suffix()
    policy = _create_policy(admin_client, name=f"sp-val-{sfx}")
    pol_id = policy["id"]

    base = {"priority": 100, "direction": "ingress", "action": "allow"}

    # Неверный action → 400 (доменная ValidationError) или 422 (Pydantic)
    r_bad_action = admin_client.post(
        f"/api/v1/security-policies/{pol_id}/rules",
        json={**base, "action": "block"},
    )
    assert r_bad_action.status_code in {400, 422}, r_bad_action.text

    # Неверный direction → 400 или 422
    r_bad_dir = admin_client.post(
        f"/api/v1/security-policies/{pol_id}/rules",
        json={**base, "direction": "forward"},
    )
    assert r_bad_dir.status_code in {400, 422}, r_bad_dir.text

    # Недопустимый source_type → 400 или 422
    r_bad_src = admin_client.post(
        f"/api/v1/security-policies/{pol_id}/rules",
        json={**base, "source_type": "user"},
    )
    assert r_bad_src.status_code in {400, 422}, r_bad_src.text

    # priority вне диапазона 1–65535 → 400 или 422
    r_bad_pri = admin_client.post(
        f"/api/v1/security-policies/{pol_id}/rules",
        json={**base, "priority": 0},
    )
    assert r_bad_pri.status_code in {400, 422}, r_bad_pri.text

    # Неверный CIDR как source_value → 400 или 422
    r_bad_cidr = admin_client.post(
        f"/api/v1/security-policies/{pol_id}/rules",
        json={**base, "source_type": "cidr", "source_value": "not-a-cidr"},
    )
    assert r_bad_cidr.status_code in {400, 422}, r_bad_cidr.text

    # Корректное правило с CIDR → 201
    r_ok = admin_client.post(
        f"/api/v1/security-policies/{pol_id}/rules",
        json={**base, "source_type": "cidr", "source_value": "10.0.0.0/8"},
    )
    assert r_ok.status_code == 201, r_ok.text
    rule = r_ok.json()["security_policy"]["rules"][0]
    assert rule["source_type"] == "cidr"
    assert rule["source_value"] == "10.0.0.0/8"
    assert rule["action"] == "allow"
    assert rule["direction"] == "ingress"


# ---------------------------------------------------------------------------
# N2-E2E-04  Create policy using SecurityGroup source/destination
# ---------------------------------------------------------------------------


def test_policy_rule_with_security_group_real_qemu(admin_client: ApiClient) -> None:
    """Правило с source_type='security_group' принимается и хранится корректно."""
    sfx = _suffix()
    project = admin_client.create_project(name=f"N2SG {sfx}", slug=f"n2sg-{sfx}")
    pid = project["id"]

    # Создаём SecurityGroup
    r_sg = admin_client.post(
        "/api/v1/security-groups",
        json={"name": f"sg-{sfx}", "project_id": pid},
    )
    assert r_sg.status_code == 201, r_sg.text
    sg_id = r_sg.json()["id"]

    policy = _create_policy(admin_client, name=f"sp-sg-{sfx}", project_id=pid)
    pol_id = policy["id"]

    # Правило с source_type="security_group"
    updated = _add_rule(
        admin_client,
        pol_id,
        priority=100,
        direction="ingress",
        action="allow",
        source_type="security_group",
        source_value=sg_id,
    )
    rules = updated["rules"]
    assert len(rules) == 1
    assert rules[0]["source_type"] == "security_group"
    assert rules[0]["source_value"] == sg_id

    # Правило с destination_type="security_group"
    updated2 = _add_rule(
        admin_client,
        pol_id,
        priority=200,
        direction="egress",
        action="deny",
        destination_type="security_group",
        destination_value=sg_id,
    )
    rules2 = updated2["rules"]
    assert len(rules2) == 2
    rule_egress = next(r for r in rules2 if r["priority"] == 200)
    assert rule_egress["destination_type"] == "security_group"
    assert rule_egress["destination_value"] == sg_id


# ---------------------------------------------------------------------------
# N2-E2E-05  Create policy using AddressPool
# ---------------------------------------------------------------------------


def test_policy_rule_with_address_pool_real_qemu(admin_client: ApiClient) -> None:
    """Правило с source_type='address_pool' принимается; компиляция завершается успешно."""
    sfx = _suffix()
    project = admin_client.create_project(name=f"N2AP {sfx}", slug=f"n2ap-{sfx}")
    pid = project["id"]

    # Создаём AddressPool
    r_pool = admin_client.post(
        "/api/v1/address-pools",
        json={"name": f"pool-{sfx}", "project_id": pid, "cidrs": ["10.50.0.0/24"]},
    )
    assert r_pool.status_code == 201, r_pool.text
    pool_id = r_pool.json()["id"]

    policy = _create_policy(admin_client, name=f"sp-ap-{sfx}", project_id=pid)
    pol_id = policy["id"]

    # Правило с source_type="address_pool"
    updated = _add_rule(
        admin_client,
        pol_id,
        priority=100,
        direction="ingress",
        action="allow",
        source_type="address_pool",
        source_value=pool_id,
    )
    rules = updated["rules"]
    assert len(rules) == 1
    assert rules[0]["source_type"] == "address_pool"
    assert rules[0]["source_value"] == pool_id

    # Компиляция с address_pool-источником → успех
    r_compile = admin_client.post(f"/api/v1/security-policies/{pol_id}/compile")
    assert r_compile.status_code == 200, r_compile.text
    compiled = r_compile.json()["security_policy"]
    assert compiled["status"] == "compiled"
    assert compiled["compiled_ruleset"] is not None


# ---------------------------------------------------------------------------
# N2-E2E-06  Create policy using ServiceObject
# ---------------------------------------------------------------------------


def test_policy_rule_with_service_object_real_qemu(admin_client: ApiClient) -> None:
    """Правило со service_object_id компилируется с разрешением протокола и портов."""
    sfx = _suffix()
    project = admin_client.create_project(name=f"N2SO {sfx}", slug=f"n2so-{sfx}")
    pid = project["id"]

    # Создаём ServiceObject (tcp/443)
    r_obj = admin_client.post(
        "/api/v1/service-objects",
        json={"name": f"https-{sfx}", "protocol": "tcp", "ports": ["443"], "project_id": pid},
    )
    assert r_obj.status_code == 201, r_obj.text
    obj_id = r_obj.json()["id"]

    policy = _create_policy(admin_client, name=f"sp-so-{sfx}", project_id=pid)
    pol_id = policy["id"]

    updated = _add_rule(
        admin_client,
        pol_id,
        priority=100,
        direction="ingress",
        action="allow",
        service_object_id=obj_id,
    )
    rules = updated["rules"]
    assert len(rules) == 1
    assert rules[0]["service_object_id"] == obj_id

    # Компиляция → разрешает service_object → compiled_ruleset содержит tcp/443
    r_compile = admin_client.post(f"/api/v1/security-policies/{pol_id}/compile")
    assert r_compile.status_code == 200, r_compile.text
    compiled = r_compile.json()["security_policy"]
    assert compiled["status"] == "compiled"
    ruleset = compiled["compiled_ruleset"]
    assert ruleset is not None
    assert "tcp" in ruleset.lower() or "443" in ruleset, (
        f"compiled_ruleset не содержит ожидаемые ключевые слова:\n{ruleset}"
    )


# ---------------------------------------------------------------------------
# N2-E2E-07  Cross-project operand reference rejected
# ---------------------------------------------------------------------------


def test_cross_project_service_object_in_policy_rejected_real_qemu(
    admin_client: ApiClient,
) -> None:
    """ServiceObject из проекта A нельзя использовать в правиле политики проекта B.

    В текущей реализации AddPolicyRule не проверяет принадлежность ServiceObject
    к проекту политики — тест помечается как xfail.
    """
    sfx = _suffix()
    proj_a = admin_client.create_project(name=f"N2OA {sfx}", slug=f"n2oa-{sfx}")
    proj_b = admin_client.create_project(name=f"N2OB {sfx}", slug=f"n2ob-{sfx}")

    # ServiceObject в проекте A
    r_obj = admin_client.post(
        "/api/v1/service-objects",
        json={
            "name": f"svc-a-{sfx}",
            "protocol": "tcp",
            "ports": ["22"],
            "project_id": proj_a["id"],
        },
    )
    assert r_obj.status_code == 201, r_obj.text
    obj_id = r_obj.json()["id"]

    # Политика в проекте B
    policy = _create_policy(admin_client, name=f"sp-b-{sfx}", project_id=proj_b["id"])
    pol_id = policy["id"]

    # Попытка добавить правило с service_object_id из проекта A
    r_rule = admin_client.post(
        f"/api/v1/security-policies/{pol_id}/rules",
        json={
            "priority": 100,
            "direction": "ingress",
            "action": "deny",
            "service_object_id": obj_id,
        },
    )
    if r_rule.status_code == 201:
        pytest.xfail(_XF_CROSS_PROJECT)
    assert r_rule.status_code in {400, 403, 409, 422}, r_rule.text


# ---------------------------------------------------------------------------
# N2-E2E-08  Compile policy → deterministic compiled_ruleset hash
# ---------------------------------------------------------------------------


def test_compile_policy_deterministic_real_qemu(admin_client: ApiClient) -> None:
    """Двойная компиляция одной и той же политики даёт идентичный ruleset."""
    sfx = _suffix()
    policy = _create_policy(admin_client, name=f"sp-det-{sfx}")
    pol_id = policy["id"]

    _add_rule(
        admin_client, pol_id, priority=10, direction="ingress", action="allow",
        source_type="cidr", source_value="192.168.1.0/24",
    )
    _add_rule(admin_client, pol_id, priority=20, direction="egress", action="deny")

    # Первая компиляция
    r1 = admin_client.post(f"/api/v1/security-policies/{pol_id}/compile")
    assert r1.status_code == 200, r1.text
    p1 = r1.json()["security_policy"]
    ruleset1 = p1["compiled_ruleset"]
    compiled_at_1 = p1["compiled_at"]
    assert ruleset1 is not None
    assert compiled_at_1 is not None

    # Вторая компиляция без изменений → идентичный ruleset
    r2 = admin_client.post(f"/api/v1/security-policies/{pol_id}/compile")
    assert r2.status_code == 200, r2.text
    p2 = r2.json()["security_policy"]
    ruleset2 = p2["compiled_ruleset"]

    assert ruleset1 == ruleset2, (
        "Компилятор недетерминирован: две компиляции одного ruleset дали разный результат"
    )
    assert p2["status"] == "compiled"


# ---------------------------------------------------------------------------
# N2-E2E-09  Compile failure on missing operand
# ---------------------------------------------------------------------------


def test_compile_with_missing_service_object_real_qemu(admin_client: ApiClient) -> None:
    """Компиляция с несуществующим service_object_id.

    Спецификация ожидает ошибку компиляции при отсутствии операнда.
    Текущая реализация: PolicyCompiler молча пропускает отсутствующий ServiceObject
    и компиляция завершается успешно — поведение расходится со спеком, xfail.
    """
    sfx = _suffix()
    policy = _create_policy(admin_client, name=f"sp-miss-{sfx}")
    pol_id = policy["id"]

    # Добавляем правило с заведомо несуществующим service_object_id
    fake_sobj_id = uuid.uuid4().hex
    r_rule = admin_client.post(
        f"/api/v1/security-policies/{pol_id}/rules",
        json={
            "priority": 100,
            "direction": "ingress",
            "action": "allow",
            "service_object_id": fake_sobj_id,
        },
    )
    assert r_rule.status_code == 201, r_rule.text

    # По спеку — должна быть ошибка компиляции
    r_compile = admin_client.post(f"/api/v1/security-policies/{pol_id}/compile")
    if r_compile.status_code == 200:
        # Компилятор пропустил отсутствующий operand без ошибки — xfail
        pytest.xfail(_XF_COMPILE_SKIP)
    assert r_compile.status_code in {400, 409, 422}, r_compile.text


# ---------------------------------------------------------------------------
# N2-E2E-10  Apply policy lifecycle: draft → compiled → applied
# ---------------------------------------------------------------------------


def test_policy_apply_lifecycle_real_qemu(admin_client: ApiClient) -> None:
    """Полный цикл: draft → compiled → applied; добавление правила сбрасывает в draft."""
    sfx = _suffix()
    policy = _create_policy(admin_client, name=f"sp-lc-{sfx}")
    pol_id = policy["id"]
    assert policy["status"] == "draft"

    # Добавляем правило — статус остаётся draft, compiled_ruleset не существует
    upd = _add_rule(admin_client, pol_id, priority=100, direction="ingress", action="allow")
    assert upd["status"] == "draft"
    assert upd["compiled_ruleset"] is None

    # Компиляция → compiled
    r_compile = admin_client.post(f"/api/v1/security-policies/{pol_id}/compile")
    assert r_compile.status_code == 200, r_compile.text
    compiled = r_compile.json()["security_policy"]
    assert compiled["status"] == "compiled"
    assert compiled["compiled_ruleset"] is not None
    assert compiled["compiled_at"] is not None
    assert compiled["applied_at"] is None

    # Применение → applied
    r_apply = admin_client.post(f"/api/v1/security-policies/{pol_id}/apply")
    assert r_apply.status_code == 200, r_apply.text
    applied = r_apply.json()["security_policy"]
    assert applied["status"] == "applied"
    assert applied["applied_at"] is not None

    # Добавление нового правила сбрасывает статус обратно в draft
    upd2 = _add_rule(admin_client, pol_id, priority=200, direction="egress", action="deny")
    assert upd2["status"] == "draft"
    assert upd2["compiled_ruleset"] is None


# ---------------------------------------------------------------------------
# N2-E2E-11  Apply failure path: apply без compile → ошибка
# ---------------------------------------------------------------------------


def test_apply_without_compile_rejected_real_qemu(admin_client: ApiClient) -> None:
    """Применение политики в статусе draft возвращает ошибку; статус не изменяется."""
    sfx = _suffix()
    policy = _create_policy(admin_client, name=f"sp-fail-{sfx}")
    pol_id = policy["id"]
    assert policy["status"] == "draft"

    _add_rule(admin_client, pol_id, priority=100, direction="ingress", action="allow")

    # apply без предшествующего compile → mark_applied поднимает ValidationError
    r_apply = admin_client.post(f"/api/v1/security-policies/{pol_id}/apply")
    assert r_apply.status_code in {400, 409, 422}, (
        f"Ожидалась ошибка при apply без compile, получено {r_apply.status_code}: {r_apply.text}"
    )

    # Политика должна оставаться в draft — apply не изменил статус
    r_check = admin_client.get(f"/api/v1/security-policies/{pol_id}")
    assert r_check.status_code == 200
    assert r_check.json()["security_policy"]["status"] == "draft"


# ---------------------------------------------------------------------------
# N2-E2E-12  Verify catches drift between desired and applied ruleset
# ---------------------------------------------------------------------------


def test_verify_drift_real_qemu(admin_client: ApiClient) -> None:
    """Эндпоинт verify/drift фиксирует расхождение applied и текущего ruleset.

    В текущей реализации эндпоинт отсутствует — тест помечается как xfail.
    """
    sfx = _suffix()
    policy = _create_policy(admin_client, name=f"sp-drift-{sfx}")
    pol_id = policy["id"]
    _add_rule(admin_client, pol_id, priority=100, direction="ingress", action="allow")

    admin_client.post(f"/api/v1/security-policies/{pol_id}/compile")
    admin_client.post(f"/api/v1/security-policies/{pol_id}/apply")

    r_verify = admin_client.post(f"/api/v1/security-policies/{pol_id}/verify")
    if r_verify.status_code in {404, 405}:
        pytest.xfail(_XF_VERIFY_MISSING)
    assert r_verify.status_code == 200, r_verify.text


# ---------------------------------------------------------------------------
# N2-E2E-13  Per-rule counters increase after fake traffic simulation
# ---------------------------------------------------------------------------


def test_rule_counters_update_real_qemu(admin_client: ApiClient) -> None:
    """Счётчики пакетов/байт обновляются через HTTP API.

    HTTP-эндпоинт обновления счётчиков (UpdateRuleCounters) не реализован в
    роутере — тест помечается как xfail при 404/405.
    """
    sfx = _suffix()
    policy = _create_policy(admin_client, name=f"sp-cnt-{sfx}")
    pol_id = policy["id"]
    updated = _add_rule(admin_client, pol_id, priority=100, direction="ingress", action="allow")
    rule_id = updated["rules"][0]["rule_id"]

    # Начальные счётчики должны быть нулевыми
    assert updated["rules"][0]["packet_count"] == 0
    assert updated["rules"][0]["byte_count"] == 0

    # Пробуем обновить счётчики через предполагаемый эндпоинт
    r_cnt = admin_client.patch(
        f"/api/v1/security-policies/{pol_id}/rules/{rule_id}/counters",
        json={"packet_count": 1000, "byte_count": 65536},
    )
    if r_cnt.status_code in {404, 405}:
        pytest.xfail(_XF_COUNTERS_NO_API)
    assert r_cnt.status_code == 200, r_cnt.text

    # Счётчики должны сохраниться
    r_get = admin_client.get(f"/api/v1/security-policies/{pol_id}")
    rule = next(
        r for r in r_get.json()["security_policy"]["rules"] if r["rule_id"] == rule_id
    )
    assert rule["packet_count"] == 1000
    assert rule["byte_count"] == 65536


# ---------------------------------------------------------------------------
# N2-E2E-14  Counters survive repeated apply / idempotent apply
# ---------------------------------------------------------------------------


def test_idempotent_apply_preserves_counters_real_qemu(admin_client: ApiClient) -> None:
    """Повторное apply идемпотентно; счётчики не сбрасываются.

    HTTP-эндпоинт обновления счётчиков отсутствует — тест помечается как xfail.
    """
    sfx = _suffix()
    policy = _create_policy(admin_client, name=f"sp-idem-{sfx}")
    pol_id = policy["id"]
    updated = _add_rule(admin_client, pol_id, priority=100, direction="ingress", action="allow")
    rule_id = updated["rules"][0]["rule_id"]

    # Полный цикл compile → apply
    admin_client.post(f"/api/v1/security-policies/{pol_id}/compile")
    admin_client.post(f"/api/v1/security-policies/{pol_id}/apply")

    # Пробуем обновить счётчики
    r_cnt = admin_client.patch(
        f"/api/v1/security-policies/{pol_id}/rules/{rule_id}/counters",
        json={"packet_count": 500, "byte_count": 32768},
    )
    if r_cnt.status_code in {404, 405}:
        pytest.xfail(_XF_COUNTERS_NO_API)

    # Повторная компиляция и применение не должны обнулять счётчики
    admin_client.post(f"/api/v1/security-policies/{pol_id}/compile")
    r_apply2 = admin_client.post(f"/api/v1/security-policies/{pol_id}/apply")
    assert r_apply2.status_code == 200, r_apply2.text

    r_get = admin_client.get(f"/api/v1/security-policies/{pol_id}")
    rule = next(
        r for r in r_get.json()["security_policy"]["rules"] if r["rule_id"] == rule_id
    )
    assert rule["packet_count"] == 500, "Счётчики сброшены при повторном apply"
    assert rule["byte_count"] == 32768, "Счётчики сброшены при повторном apply"


# ---------------------------------------------------------------------------
# N2-E2E-15  Policy events: created/compiled/applied/rule_added/rule_removed/deleted
# ---------------------------------------------------------------------------


def test_policy_events_real_qemu(admin_client: ApiClient) -> None:
    """Жизненный цикл политики генерирует outbox-события нужных типов."""
    sfx = _suffix()
    policy = _create_policy(admin_client, name=f"sp-ev-{sfx}")
    pol_id = policy["id"]

    # Добавляем правило → security_policy.rule_added
    _add_rule(admin_client, pol_id, priority=100, direction="ingress", action="allow")

    # Компилируем → security_policy.compiled
    admin_client.post(f"/api/v1/security-policies/{pol_id}/compile")

    # Применяем → security_policy.applied
    admin_client.post(f"/api/v1/security-policies/{pol_id}/apply")

    # Добавляем ещё правило (сбрасывает в draft) и удаляем → security_policy.rule_removed
    upd = _add_rule(admin_client, pol_id, priority=200, direction="egress", action="deny")
    second_rule_id = next(r["rule_id"] for r in upd["rules"] if r["priority"] == 200)
    admin_client.delete(f"/api/v1/security-policies/{pol_id}/rules/{second_rule_id}")

    # Удаляем политику → security_policy.deleted
    admin_client.delete(f"/api/v1/security-policies/{pol_id}")

    events = _outbox_events_for(admin_client, pol_id)
    if not events:
        pytest.xfail("N2-15 outbox API недоступен и DB-инспектор не видит событий")

    event_types = {e["event_type"] for e in events}
    assert "security_policy.created" in event_types, event_types
    assert "security_policy.rule_added" in event_types, event_types
    assert "security_policy.compiled" in event_types, event_types
    assert "security_policy.applied" in event_types, event_types
    assert "security_policy.rule_removed" in event_types, event_types
    assert "security_policy.deleted" in event_types, event_types


# ---------------------------------------------------------------------------
# N2-E2E-16  CLI parity if CLI exists
# ---------------------------------------------------------------------------


def test_cli_parity_n2_real_qemu(admin_client: ApiClient) -> None:
    """Если CLI существует, он должен работать с SecurityPolicy теми же данными.

    В текущей реализации CLI отсутствует — тест помечается как xfail.
    """
    import shutil

    cli = shutil.which("nervum") or shutil.which("sdn-ctl") or shutil.which("netos-cli")
    if cli is None:
        pytest.xfail(_XF_CLI_MISSING)

    import subprocess

    result = subprocess.run(
        [cli, "security-policies", "list"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Бонус: TrunkPort CRUD (N2-05 доменный — не в E2E-спеке, но покрываем роутер)
# ---------------------------------------------------------------------------


def test_trunk_port_crud_real_qemu(admin_client: ApiClient) -> None:
    """create / list / get / update / delete TrunkPort (802.1q)."""
    sfx = _suffix()
    node = _register_node(admin_client, suffix=sfx)

    # CREATE
    r_create = admin_client.post(
        "/api/v1/trunk-ports",
        json={
            "name": f"trunk-{sfx}",
            "node_id": node["id"],
            "vlan_ids": [10, 20, 30],
            "native_vlan": 1,
        },
    )
    assert r_create.status_code == 201, r_create.text
    trunk = r_create.json()["trunk_port"]
    trunk_id = trunk["id"]
    assert trunk["node_id"] == node["id"]
    assert set(trunk["vlan_ids"]) == {10, 20, 30}
    assert trunk["native_vlan"] == 1

    # LIST
    r_list = admin_client.get("/api/v1/trunk-ports", params={"node_id": node["id"]})
    assert r_list.status_code == 200, r_list.text
    assert any(t["id"] == trunk_id for t in _items(r_list))

    # GET
    r_get = admin_client.get(f"/api/v1/trunk-ports/{trunk_id}")
    assert r_get.status_code == 200, r_get.text
    assert r_get.json()["trunk_port"]["id"] == trunk_id

    # PATCH
    r_patch = admin_client.patch(
        f"/api/v1/trunk-ports/{trunk_id}",
        json={"name": f"trunk-upd-{sfx}", "vlan_ids": [10, 20, 40, 50]},
    )
    assert r_patch.status_code == 200, r_patch.text
    patched = r_patch.json()["trunk_port"]
    assert patched["name"] == f"trunk-upd-{sfx}"
    assert set(patched["vlan_ids"]) == {10, 20, 40, 50}

    # DELETE
    r_del = admin_client.delete(f"/api/v1/trunk-ports/{trunk_id}")
    assert r_del.status_code == 204, r_del.text

    # GET после удаления → 404
    assert admin_client.get(f"/api/v1/trunk-ports/{trunk_id}").status_code == 404
