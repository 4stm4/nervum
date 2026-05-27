"""Fixtures для DP (dataplane) E2E тестов.

Группа отдельна от e2e_qemu: требует реального OVS и nftables в QEMU-госте.
Активируется переменной окружения E2E_DP_RUN=1.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.e2e_dp.helpers.ssh_exec import SshExecutor
from tests.e2e_qemu.helpers.api_client import ApiClient


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("E2E_DP_RUN") == "1":
        return
    skip = pytest.mark.skip(reason="DP dataplane tests: set E2E_DP_RUN=1 to run")
    dp_dir = Path(__file__).parent
    for item in items:
        if dp_dir in Path(item.path).parents:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def dp_ssh() -> SshExecutor:
    """SSH-исполнитель для команд в QEMU-госте."""
    host = os.environ.get("E2E_QEMU_HOST", "127.0.0.1")
    port = int(os.environ.get("E2E_QEMU_SSH_PORT", "10022"))
    user = os.environ.get("E2E_QEMU_USER", "root")
    return SshExecutor(host=host, port=port, user=user)


@pytest.fixture(scope="session")
def dp_agent_url() -> str:
    """URL агента NetOS Agent внутри QEMU (туннель на localhost)."""
    return os.environ.get("E2E_DP_AGENT_URL", "http://127.0.0.1:19100")


def _wait_for_agent(url: str, *, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        for path in ("/health", "/healthz", "/v1/system/health"):
            try:
                r = httpx.get(f"{url.rstrip('/')}{path}", timeout=3.0)
                if 200 <= r.status_code < 500:
                    return
                last_err = f"{path}: HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                last_err = str(exc)
        time.sleep(2.0)
    raise AssertionError(f"NetOS Agent не готов по адресу {url}: {last_err}")


@pytest.fixture(scope="session", autouse=True)
def dp_agent_ready(dp_agent_url: str) -> None:
    """Ждёт готовности агента перед запуском тестов."""
    _wait_for_agent(dp_agent_url)


@pytest.fixture()
def agent_client(dp_agent_url: str) -> Iterator[httpx.Client]:
    """HTTP-клиент к NetOS Agent (без аутентификации)."""
    client = httpx.Client(base_url=dp_agent_url, timeout=30.0)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def e2e_qemu_api_url() -> str:
    return os.environ.get("E2E_QEMU_API_URL", "http://127.0.0.1:18080")


@pytest.fixture(scope="session")
def admin_token() -> str:
    return os.environ.get("SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN", "e2e-admin-token")


@pytest.fixture()
def admin_client(e2e_qemu_api_url: str, admin_token: str) -> Iterator[ApiClient]:
    """Клиент к SDN-контроллеру с токеном администратора."""
    client = ApiClient(e2e_qemu_api_url, token=admin_token)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def dp_traffic_ns(dp_ssh: SshExecutor) -> dict[str, str]:
    """Создаёт пространства имён для тестов трафика (сессионный fixture).

    Топология:
      ns-dp-src (10.99.10.1/24) ←veth→ root (10.99.10.254/24)
                                         root (10.99.11.254/24) ←veth→ ns-dp-dst (10.99.11.1/24)
    Трафик между ns-dp-src и ns-dp-dst форсируется через kernel IP forward,
    куда и подключён nftables forward hook.
    """
    cmds = [
        # Пространства имён
        "ip netns add ns-dp-src 2>/dev/null || true",
        "ip netns add ns-dp-dst 2>/dev/null || true",
        # veth пары
        "ip link add vdp-a0 type veth peer name vdp-a1 2>/dev/null || true",
        "ip link add vdp-b0 type veth peer name vdp-b1 2>/dev/null || true",
        # Перенос концов в пространства имён
        "ip link set vdp-a0 netns ns-dp-src 2>/dev/null || true",
        "ip link set vdp-b0 netns ns-dp-dst 2>/dev/null || true",
        # Настройка root-ns интерфейсов
        "ip addr replace 10.99.10.254/24 dev vdp-a1",
        "ip link set vdp-a1 up",
        "ip addr replace 10.99.11.254/24 dev vdp-b1",
        "ip link set vdp-b1 up",
        # Настройка ns-dp-src
        "ip netns exec ns-dp-src ip addr replace 10.99.10.1/24 dev vdp-a0",
        "ip netns exec ns-dp-src ip link set vdp-a0 up",
        "ip netns exec ns-dp-src ip route replace default via 10.99.10.254",
        # Настройка ns-dp-dst
        "ip netns exec ns-dp-dst ip addr replace 10.99.11.1/24 dev vdp-b0",
        "ip netns exec ns-dp-dst ip link set vdp-b0 up",
        "ip netns exec ns-dp-dst ip route replace default via 10.99.11.254",
        # Включение IP forwarding
        "sysctl -qw net.ipv4.ip_forward=1",
    ]
    for cmd in cmds:
        dp_ssh(cmd)
    return {
        "src_ns": "ns-dp-src",
        "dst_ns": "ns-dp-dst",
        "src_ip": "10.99.10.1",
        "dst_ip": "10.99.11.1",
    }
