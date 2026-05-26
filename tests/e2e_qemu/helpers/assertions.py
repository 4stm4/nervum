"""Assertions shared by N0 QEMU E2E tests."""

from __future__ import annotations

from typing import Any

import httpx


def assert_project_id_present(payload: dict[str, Any], expected_project_id: str) -> None:
    assert "project_id" in payload, payload
    assert payload["project_id"] == expected_project_id, payload


def assert_outbox_v2_event(event: dict[str, Any], expected_project_id: str) -> None:
    assert event.get("schema_version") == 2, event
    assert event.get("project_id") == expected_project_id, event


def assert_forbidden_or_not_found(response: httpx.Response) -> None:
    assert response.status_code in {403, 404}, response.text
