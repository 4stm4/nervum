"""Polling helpers for real-environment E2E tests."""

from __future__ import annotations

import time
from typing import Any

import httpx

from tests.e2e_qemu.helpers.api_client import ApiClient

TERMINAL_OPERATION_STATUSES = {"succeeded", "failed", "cancelled", "rolled_back"}
HEALTH_PATHS = (
    "/health",
    "/healthz",
    "/readyz",
    "/api/v1/healthz",
    "/api/v1/readyz",
    "/api/v1/health",
    "/api/v1/livez",
    "/api/v1/version",
)


def wait_for_api_ready(base_url: str, *, timeout: float = 60.0) -> str:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        for path in HEALTH_PATHS:
            try:
                response = httpx.get(f"{base_url.rstrip('/')}{path}", timeout=3.0)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                continue
            if 200 <= response.status_code < 500:
                return path
            last_error = f"{path}: HTTP {response.status_code}"
        time.sleep(1.0)
    raise AssertionError(f"Nervum API did not become ready at {base_url}: {last_error}")


def wait_operation_terminal(
    client: ApiClient,
    operation_id: str,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/operations/{operation_id}")
        assert response.status_code == 200, response.text
        last = dict(response.json())
        if str(last["status"]) in TERMINAL_OPERATION_STATUSES:
            return last
        time.sleep(0.5)
    raise AssertionError(f"operation {operation_id} did not reach terminal status: {last}")
