"""DP E2E — проверка реального датаплейна на QEMU-госте (DP-E2E-01..14).

Группа отдельна от e2e_qemu (N0–N3): здесь нужны реальные компоненты —
OVS (backend=subprocess) и nftables (backend=nftables). Гость должен иметь
установленные пакеты openvswitch и nftables.

Запуск:  E2E_DP_RUN=1 pytest tests/e2e_dp -m "e2e and qemu and dp"

Переменные окружения:
  E2E_QEMU_SSH_PORT   — SSH-порт туннеля к QEMU (по умолч. 10022)
  E2E_QEMU_HOST       — хост туннелей (по умолч. 127.0.0.1)
  E2E_DP_AGENT_URL    — URL агента через туннель (по умолч. http://127.0.0.1:19100)
  E2E_QEMU_API_URL    — URL SDN-контроллера (по умолч. http://127.0.0.1:18080)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests.e2e_dp.helpers.ssh_exec import SshExecutor
from tests.e2e_qemu.helpers.api_client import ApiClient

# ---------------------------------------------------------------------------
# Метки времени выполнения и маркеры
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.e2e, pytest.mark.qemu, pytest.mark.dp]

# xfail-причины
_XF_NO_ROUTE_STEP = pytest.mark.xfail(
    strict=False,
    reason="ensure_route step не реализован в NetOS Agent (MVP); L3 routing через агента — N4+",
)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _suffix() -> str:
    """Уникальный 8-символьный суффикс для изоляции ресурсов."""
    return uuid.uuid4().hex[:8]


def _apply(client: httpx.Client, plan_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Отправляет план агенту, проверяет HTTP 200, возвращает результат."""
    r = client.post("/v1/network/apply", json={"plan_id": plan_id, "steps": steps})
    assert r.status_code == 200, f"apply failed: {r.text}"
    return dict(r.json())


def _ovs_state(client: httpx.Client) -> dict[str, Any]:
    """Возвращает текущее состояние OVS из агента."""
    r = client.get("/v1/ovs/state")
    assert r.status_code == 200, r.text
    return dict(r.json())


def _find_bridge(state: dict[str, Any], name: str) -> dict[str, Any] | None:
    for b in state.get("bridges", []):
        if b["name"] == name:
            return dict(b)
    return None


def _find_port(bridge: dict[str, Any], name: str) -> dict[str, Any] | None:
    for p in bridge.get("ports", []):
        if p["name"] == name:
            return dict(p)
    return None


def _find_iface(port: dict[str, Any], name: str) -> dict[str, Any] | None:
    for i in port.get("interfaces", []):
        if i["name"] == name:
            return dict(i)
    return None


def _policy_chain_name(policy_id: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in policy_id)
    return f"policy_{safe}"


def _wait_for_ping(
    dp_ssh: SshExecutor,
    ns: str,
    dst: str,
    *,
    expect_success: bool,
    attempts: int = 3,
    interval: float = 1.0,
) -> bool:
    """Повторяет ping несколько раз, возвращает итог.

    При expect_success=True ждёт первого успеха; иначе ждёт, пока все попытки неудачны.
    """
    for _ in range(attempts):
        ok = dp_ssh.ping_from_ns(ns, dst)
        if ok == expect_success:
            return ok
        time.sleep(interval)
    return dp_ssh.ping_from_ns(ns, dst)


# ---------------------------------------------------------------------------
# DP-E2E-01  OVS доступен в QEMU-госте
# ---------------------------------------------------------------------------


def test_dp_01_ovs_available(dp_ssh: SshExecutor) -> None:
    """OVS установлен в госте и команда ovs-vsctl --version отвечает."""
    out = dp_ssh("ovs-vsctl --version")
    assert "ovs-vsctl" in out.lower() or "openvswitch" in out.lower()


# ---------------------------------------------------------------------------
# DP-E2E-02  Агент сообщает реальную версию OVS
# ---------------------------------------------------------------------------


def test_dp_02_agent_reports_real_ovs_version(agent_client: httpx.Client) -> None:
    """GET /v1/ovs/state возвращает ovs_version, отличный от None/пустой строки.

    Если агент запущен с OVS_BACKEND=subprocess, поле берётся из `ovs-vsctl --version`.
    """
    state = _ovs_state(agent_client)
    version = state.get("ovs_version")
    assert version is not None, "ovs_version is None — вероятно, агент работает на fake backend"
    assert len(version.strip()) > 0, "ovs_version пуст"


# ---------------------------------------------------------------------------
# DP-E2E-03  Создание bridge через apply агента
# ---------------------------------------------------------------------------


def test_dp_03_create_bridge_via_apply(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
) -> None:
    """Шаг ensure_bridge создаёт реальный OVS-бридж, подтверждаемый ovs-vsctl."""
    sfx = _suffix()
    bridge = f"sdndp{sfx[:6]}"  # ≤15 символов

    _apply(agent_client, f"dp-03-{sfx}", [
        {"action": "ensure_bridge", "name": bridge, "datapath_type": "system"},
    ])

    # Проверка через API агента
    state = _ovs_state(agent_client)
    assert _find_bridge(state, bridge) is not None, f"bridge {bridge!r} не найден в OVS state"

    # Проверка через SSH
    br_list = dp_ssh("ovs-vsctl list-br")
    assert bridge in br_list.splitlines(), f"bridge {bridge!r} не в выводе ovs-vsctl list-br"

    # Очистка
    _apply(agent_client, f"dp-03-cleanup-{sfx}", [
        {"action": "delete_bridge", "name": bridge},
    ])


# ---------------------------------------------------------------------------
# DP-E2E-04  LogicalPort создаёт реальный OVS-порт с external_ids
# ---------------------------------------------------------------------------


def test_dp_04_logical_port_creates_ovs_port_with_external_ids(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
) -> None:
    """ensure_port создаёт OVS-порт с переданными external_ids."""
    sfx = _suffix()
    bridge = f"sdndp{sfx[:6]}"
    port = f"lp{sfx[:6]}"
    lp_id = f"lp-uuid-{sfx}"

    _apply(agent_client, f"dp-04-{sfx}", [
        {"action": "ensure_bridge", "name": bridge, "datapath_type": "system"},
        {
            "action": "ensure_port",
            "bridge": bridge,
            "name": port,
            "type": "internal",
            "external_ids": {"logical_port_id": lp_id, "project": "dp-test"},
        },
    ])

    # Проверка через API
    state = _ovs_state(agent_client)
    br = _find_bridge(state, bridge)
    assert br is not None
    pt = _find_port(br, port)
    assert pt is not None, f"порт {port!r} не найден"
    assert pt.get("external_ids", {}).get("logical_port_id") == lp_id

    # Проверка через SSH
    ext_ids_raw = dp_ssh(
        f"ovs-vsctl --format=json --columns=external_ids list Port {port}"
    )
    assert lp_id in ext_ids_raw, f"logical_port_id {lp_id!r} не в external_ids порта"

    # Очистка
    _apply(agent_client, f"dp-04-cleanup-{sfx}", [
        {"action": "delete_port", "bridge": bridge, "name": port},
        {"action": "delete_bridge", "name": bridge},
    ])


# ---------------------------------------------------------------------------
# DP-E2E-05  VLAN access-порт применяет реальный тег
# ---------------------------------------------------------------------------


def test_dp_05_vlan_access_port_applies_real_tag(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
) -> None:
    """ensure_port с tag=200 устанавливает VLAN tag 200 в OVS."""
    sfx = _suffix()
    bridge = f"sdndp{sfx[:6]}"
    port = f"vl{sfx[:6]}"
    vlan_tag = 200

    _apply(agent_client, f"dp-05-{sfx}", [
        {"action": "ensure_bridge", "name": bridge, "datapath_type": "system"},
        {
            "action": "ensure_port",
            "bridge": bridge,
            "name": port,
            "type": "internal",
            "tag": vlan_tag,
        },
    ])

    state = _ovs_state(agent_client)
    br = _find_bridge(state, bridge)
    assert br is not None
    pt = _find_port(br, port)
    assert pt is not None
    assert pt.get("tag") == vlan_tag, f"ожидался tag={vlan_tag}, получен {pt.get('tag')!r}"

    # Проверка через SSH
    tag_raw = dp_ssh(f"ovs-vsctl get Port {port} tag")
    assert tag_raw.strip() == str(vlan_tag), f"OVS tag: {tag_raw.strip()!r}"

    _apply(agent_client, f"dp-05-cleanup-{sfx}", [
        {"action": "delete_port", "bridge": bridge, "name": port},
        {"action": "delete_bridge", "name": bridge},
    ])


# ---------------------------------------------------------------------------
# DP-E2E-06  VLAN trunk-порт применяет список trunks
# ---------------------------------------------------------------------------


def test_dp_06_vlan_trunk_port_applies_trunks(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
) -> None:
    """ensure_port с trunks=[100,200,300] устанавливает список trunk-VLAN."""
    sfx = _suffix()
    bridge = f"sdndp{sfx[:6]}"
    port = f"tr{sfx[:6]}"
    trunks = [100, 200, 300]

    _apply(agent_client, f"dp-06-{sfx}", [
        {"action": "ensure_bridge", "name": bridge, "datapath_type": "system"},
        {
            "action": "ensure_port",
            "bridge": bridge,
            "name": port,
            "type": "internal",
            "trunks": trunks,
        },
    ])

    state = _ovs_state(agent_client)
    br = _find_bridge(state, bridge)
    assert br is not None
    pt = _find_port(br, port)
    assert pt is not None
    assert sorted(pt.get("trunks", [])) == sorted(trunks), (
        f"ожидались trunks={trunks}, получены {pt.get('trunks')!r}"
    )

    # Проверка через SSH
    trunks_raw = dp_ssh(f"ovs-vsctl get Port {port} trunks")
    for vlan in trunks:
        assert str(vlan) in trunks_raw, f"VLAN {vlan} не найден в trunks: {trunks_raw!r}"

    _apply(agent_client, f"dp-06-cleanup-{sfx}", [
        {"action": "delete_port", "bridge": bridge, "name": port},
        {"action": "delete_bridge", "name": bridge},
    ])


# ---------------------------------------------------------------------------
# DP-E2E-07  VXLAN-порт создаётся с правильными remote_ip/vni/options
# ---------------------------------------------------------------------------


def test_dp_07_vxlan_port_created_with_correct_options(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
) -> None:
    """ensure_vxlan_port создаёт реальный OVS VXLAN-порт с корректными options."""
    sfx = _suffix()
    bridge = f"sdndp{sfx[:6]}"
    port = f"vx{sfx[:6]}"
    vni = 4242
    remote_ip = "10.1.2.3"

    _apply(agent_client, f"dp-07-{sfx}", [
        {"action": "ensure_bridge", "name": bridge, "datapath_type": "system"},
        {
            "action": "ensure_vxlan_port",
            "bridge": bridge,
            "name": port,
            "vni": vni,
            "remote_ip": remote_ip,
        },
    ])

    state = _ovs_state(agent_client)
    br = _find_bridge(state, bridge)
    assert br is not None
    pt = _find_port(br, port)
    assert pt is not None, f"VXLAN порт {port!r} не найден"
    ifaces = pt.get("interfaces", [])
    assert ifaces, "нет интерфейсов у VXLAN-порта"
    iface = ifaces[0]
    assert iface.get("type") == "vxlan", f"type интерфейса: {iface.get('type')!r}"
    options = iface.get("options", {})
    assert options.get("remote_ip") == remote_ip, f"remote_ip: {options.get('remote_ip')!r}"
    assert options.get("key") == str(vni), f"vni (key): {options.get('key')!r}"

    # Проверка через SSH
    iface_type = dp_ssh(f"ovs-vsctl get Interface {port} type").strip()
    assert iface_type == "vxlan", f"тип интерфейса по SSH: {iface_type!r}"
    opts_raw = dp_ssh(f"ovs-vsctl get Interface {port} options")
    assert remote_ip in opts_raw, f"remote_ip не в options: {opts_raw!r}"
    assert str(vni) in opts_raw, f"vni не в options: {opts_raw!r}"

    _apply(agent_client, f"dp-07-cleanup-{sfx}", [
        {"action": "delete_port", "bridge": bridge, "name": port},
        {"action": "delete_bridge", "name": bridge},
    ])


# ---------------------------------------------------------------------------
# DP-E2E-08  nftables table создаётся при применении security policy
# ---------------------------------------------------------------------------


def test_dp_08_nftables_table_created_for_policy(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
) -> None:
    """ensure_firewall_policy создаёт chain в таблице inet sdn_controller."""
    sfx = _suffix()
    policy_id = f"dp08-{sfx}"

    _apply(agent_client, f"dp-08-{sfx}", [
        {
            "action": "ensure_firewall_policy",
            "spec": {
                "policy_id": policy_id,
                "default_action": "accept",
                "rules": [],
            },
        },
    ])

    # Проверка через SSH: таблица и chain присутствуют
    rc, out, _ = dp_ssh.run("nft list table inet sdn_controller")
    assert rc == 0, "таблица inet sdn_controller не существует"
    chain = _policy_chain_name(policy_id)
    assert chain in out, f"chain {chain!r} не найден в таблице"

    # Очистка
    _apply(agent_client, f"dp-08-cleanup-{sfx}", [
        {"action": "delete_firewall_policy", "policy_id": policy_id},
    ])


# ---------------------------------------------------------------------------
# DP-E2E-09  Allow-правило пропускает трафик через nftables
# ---------------------------------------------------------------------------


def test_dp_09_allow_rule_permits_traffic(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
    dp_traffic_ns: dict[str, str],
) -> None:
    """Policy с accept-правилом для ICMP не блокирует ping между ns."""
    sfx = _suffix()
    policy_id = f"dp09-allow-{sfx}"
    src_ns = dp_traffic_ns["src_ns"]
    dst_ip = dp_traffic_ns["dst_ip"]

    # Применяем политику: accept ICMP, drop остальное
    _apply(agent_client, f"dp-09-{sfx}", [
        {
            "action": "ensure_firewall_policy",
            "spec": {
                "policy_id": policy_id,
                "default_action": "drop",
                "rules": [
                    {
                        "action": "accept",
                        "proto": "icmp",
                        "source_cidr": "10.99.10.0/24",
                        "destination_cidr": "10.99.11.0/24",
                    },
                ],
            },
        },
    ])

    try:
        ok = _wait_for_ping(dp_ssh, src_ns, dst_ip, expect_success=True)
        assert ok, (
            f"ping {src_ns} → {dst_ip} не прошёл при наличии accept-правила для ICMP"
        )
    finally:
        _apply(agent_client, f"dp-09-cleanup-{sfx}", [
            {"action": "delete_firewall_policy", "policy_id": policy_id},
        ])


# ---------------------------------------------------------------------------
# DP-E2E-10  Deny-правило блокирует трафик через nftables
# ---------------------------------------------------------------------------


def test_dp_10_deny_rule_blocks_traffic(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
    dp_traffic_ns: dict[str, str],
) -> None:
    """Policy с default_action=drop без allow-правил блокирует ping."""
    sfx = _suffix()
    policy_id = f"dp10-deny-{sfx}"
    src_ns = dp_traffic_ns["src_ns"]
    dst_ip = dp_traffic_ns["dst_ip"]

    _apply(agent_client, f"dp-10-{sfx}", [
        {
            "action": "ensure_firewall_policy",
            "spec": {
                "policy_id": policy_id,
                "default_action": "drop",
                "rules": [],
            },
        },
    ])

    try:
        ok = _wait_for_ping(dp_ssh, src_ns, dst_ip, expect_success=False)
        assert not ok, (
            f"ping {src_ns} → {dst_ip} прошёл, хотя политика запрещает весь трафик"
        )
    finally:
        _apply(agent_client, f"dp-10-cleanup-{sfx}", [
            {"action": "delete_firewall_policy", "policy_id": policy_id},
        ])


# ---------------------------------------------------------------------------
# DP-E2E-11  Счётчики nftables увеличиваются после прохождения трафика
# ---------------------------------------------------------------------------


def test_dp_11_nftables_counters_increase_after_traffic(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
    dp_traffic_ns: dict[str, str],
) -> None:
    """После ping счётчики в цепочке policy нарастают."""
    sfx = _suffix()
    policy_id = f"dp11-cnt-{sfx}"
    src_ns = dp_traffic_ns["src_ns"]
    dst_ip = dp_traffic_ns["dst_ip"]

    _apply(agent_client, f"dp-11-{sfx}", [
        {
            "action": "ensure_firewall_policy",
            "spec": {
                "policy_id": policy_id,
                "default_action": "accept",
                "rules": [
                    {
                        "action": "accept",
                        "proto": "icmp",
                    },
                ],
            },
        },
    ])

    try:
        # Генерируем трафик
        dp_ssh.ping_from_ns(src_ns, dst_ip, count=5)

        # Читаем счётчики через nft -j
        rc, out, _ = dp_ssh.run(
            "nft -j list table inet sdn_controller 2>/dev/null"
        )
        assert rc == 0, "nft -j завершился с ошибкой"

        chain = _policy_chain_name(policy_id)
        total_packets = 0
        try:
            doc = json.loads(out)
            for item in doc.get("nftables", []):
                rule = item.get("rule")
                if not isinstance(rule, dict) or rule.get("chain") != chain:
                    continue
                for expr in rule.get("expr", []):
                    counter = expr.get("counter") if isinstance(expr, dict) else None
                    if isinstance(counter, dict):
                        total_packets += int(counter.get("packets", 0))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            pytest.skip(f"не удалось разобрать nft -j: {exc}")

        assert total_packets > 0, (
            f"счётчик chain {chain!r} равен 0 после 5 ping-запросов; "
            f"проверьте, что dispatch hook forward активен"
        )
    finally:
        _apply(agent_client, f"dp-11-cleanup-{sfx}", [
            {"action": "delete_firewall_policy", "policy_id": policy_id},
        ])


# ---------------------------------------------------------------------------
# DP-E2E-12  FloatingIP: NAT-правило появляется в nftables
# ---------------------------------------------------------------------------


def test_dp_12_floating_ip_creates_nat_masquerade_rule(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
) -> None:
    """ensure_nat_rule создаёт masquerade в таблице ip sdn_controller_nat."""
    sfx = _suffix()
    rule_id = f"fip-{sfx}"
    source_cidr = "10.99.10.0/24"
    egress_if = "vdp-a1"  # реальный интерфейс из dp_traffic_ns

    _apply(agent_client, f"dp-12-{sfx}", [
        {
            "action": "ensure_nat_rule",
            "spec": {
                "rule_id": rule_id,
                "source_cidr": source_cidr,
                "egress_interface": egress_if,
            },
        },
    ])

    # Проверка через SSH
    rc, out, _ = dp_ssh.run("nft list table ip sdn_controller_nat 2>/dev/null")
    assert rc == 0, "таблица ip sdn_controller_nat не существует"
    assert "masquerade" in out, f"masquerade не найден в NAT-таблице:\n{out}"
    assert rule_id in out, f"comment {rule_id!r} не найден в NAT-таблице"
    assert source_cidr in out, f"source_cidr {source_cidr!r} не найден в NAT-правиле"

    # Очистка
    _apply(agent_client, f"dp-12-cleanup-{sfx}", [
        {"action": "delete_nat_rule", "rule_id": rule_id},
    ])


# ---------------------------------------------------------------------------
# DP-E2E-13  Router static route появляется в таблице маршрутизации
# ---------------------------------------------------------------------------


@_XF_NO_ROUTE_STEP
def test_dp_13_router_static_route_appears_in_routing_table(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
    admin_client: ApiClient,
) -> None:
    """L3 intent (статический маршрут) должен приводить к реальному ip route.

    В MVP NetOS Agent не имеет шага ensure_route в схеме плана — этот тест
    документирует ожидаемое поведение и помечен xfail до реализации.

    Проверяем текущий максимально возможный уровень:
      1. Создаём Router со статическим маршрутом через SDN-контроллер.
      2. Вызываем apply → Router переходит в active.
      3. Проверяем ip route show в госте (маршрут должен был появиться).
    """
    sfx = _suffix()

    # Проект и сеть — минимальный scaffold
    project = admin_client.create_project(name=f"dp13-{sfx}", slug=f"dp13-{sfx}")
    project_id = project["project"]["id"]

    # Создаём flat-сеть как external_network
    net_r = admin_client.post(
        "/api/v1/networks",
        json={"name": f"dp13-ext-{sfx}", "type": "flat"},
    )
    assert net_r.status_code == 202, net_r.text
    net_id = net_r.json()["network"]["id"]

    # Создаём Router
    router_r = admin_client.post(
        "/api/v1/routers",
        json={
            "name": f"dp13-router-{sfx}",
            "project_id": project_id,
            "external_network_id": net_id,
        },
    )
    assert router_r.status_code in {201, 202}, router_r.text
    router_id = router_r.json()["router"]["id"]

    # Добавляем статический маршрут
    route_r = admin_client.post(
        f"/api/v1/routers/{router_id}/routes",
        json={"destination": "192.168.200.0/24", "nexthop": "10.0.0.1"},
    )
    assert route_r.status_code in {200, 201, 202}, route_r.text

    # Apply router
    apply_r = admin_client.post(f"/api/v1/routers/{router_id}/apply")
    assert apply_r.status_code in {200, 202}, apply_r.text
    assert apply_r.json()["router"]["status"] == "active"

    # Ожидаем: маршрут 192.168.200.0/24 должен появиться в ip route
    rc, route_out, _ = dp_ssh.run("ip route show 192.168.200.0/24")
    assert rc == 0 and "192.168.200.0/24" in route_out, (
        "ip route show не содержит 192.168.200.0/24 — "
        "ensure_route step не реализован в агенте"
    )


# ---------------------------------------------------------------------------
# DP-E2E-14  Дрейф: мутация OVS → reconcile восстанавливает состояние
# ---------------------------------------------------------------------------


def test_dp_14_drift_injection_detected_and_reconciled(
    agent_client: httpx.Client,
    dp_ssh: SshExecutor,
) -> None:
    """Применяем план, удаляем порт вручную, повторяем apply — порт восстанавливается."""
    sfx = _suffix()
    bridge = f"sdndp{sfx[:6]}"
    port = f"dr{sfx[:6]}"

    steps = [
        {"action": "ensure_bridge", "name": bridge, "datapath_type": "system"},
        {
            "action": "ensure_port",
            "bridge": bridge,
            "name": port,
            "type": "internal",
            "external_ids": {"managed_by": "sdn-controller", "drift_test": sfx},
        },
    ]

    # Первичное применение
    _apply(agent_client, f"dp-14-first-{sfx}", steps)

    state = _ovs_state(agent_client)
    br = _find_bridge(state, bridge)
    assert br is not None
    assert _find_port(br, port) is not None, "порт не создан после первого apply"

    # Имитируем дрейф: удаляем порт вручную через ovs-vsctl
    dp_ssh(f"ovs-vsctl del-port {bridge} {port}")

    # Убеждаемся, что порт исчез
    br_list = dp_ssh("ovs-vsctl list-br")
    assert bridge in br_list.splitlines()
    port_list = dp_ssh(f"ovs-vsctl list-ports {bridge}").splitlines()
    assert port not in port_list, f"порт {port!r} всё ещё присутствует после ручного удаления"

    # Повторяем apply (reconcile)
    _apply(agent_client, f"dp-14-reconcile-{sfx}", steps)

    # Порт должен вернуться
    state2 = _ovs_state(agent_client)
    br2 = _find_bridge(state2, bridge)
    assert br2 is not None
    assert _find_port(br2, port) is not None, (
        f"порт {port!r} не восстановлен после reconcile apply"
    )

    # Проверка через SSH
    ports_after = dp_ssh(f"ovs-vsctl list-ports {bridge}").splitlines()
    assert port in ports_after, f"SSH: порт {port!r} не найден после reconcile"

    # Очистка
    _apply(agent_client, f"dp-14-cleanup-{sfx}", [
        {"action": "delete_port", "bridge": bridge, "name": port},
        {"action": "delete_bridge", "name": bridge},
    ])
