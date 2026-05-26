from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e_qemu.helpers.api_client import ApiClient
from tests.e2e_qemu.helpers.waiters import wait_for_api_ready


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("E2E_QEMU_RUN") == "1":
        return
    skip = pytest.mark.skip(reason="QEMU E2E tests require E2E_QEMU_RUN=1")
    e2e_dir = Path(__file__).parent
    for item in items:
        if e2e_dir in Path(item.path).parents:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def e2e_qemu_api_url() -> str:
    return os.environ.get("E2E_QEMU_API_URL", "http://127.0.0.1:18080")


@pytest.fixture(scope="session", autouse=True)
def api_ready(e2e_qemu_api_url: str) -> str:
    return wait_for_api_ready(e2e_qemu_api_url)


@pytest.fixture(scope="session")
def admin_token() -> str:
    return os.environ.get("SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN", "e2e-admin-token")


@pytest.fixture()
def admin_client(e2e_qemu_api_url: str, admin_token: str) -> Iterator[ApiClient]:
    client = ApiClient(e2e_qemu_api_url, token=admin_token)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def project_a_client(e2e_qemu_api_url: str, admin_token: str) -> Iterator[ApiClient]:
    client = ApiClient(e2e_qemu_api_url, token=admin_token)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def project_b_client(e2e_qemu_api_url: str, admin_token: str) -> Iterator[ApiClient]:
    client = ApiClient(e2e_qemu_api_url, token=admin_token)
    try:
        yield client
    finally:
        client.close()
