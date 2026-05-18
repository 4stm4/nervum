"""Инварианты ``AuditEvent``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdn_controller.core.entities import AuditEvent
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import AuditEventId

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)


def _event(**overrides: object) -> AuditEvent:
    base: dict[str, object] = {
        "id": AuditEventId("audit_1"),
        "at": _NOW,
        "action": "network.create",
        "resource_type": "network",
    }
    base.update(overrides)
    return AuditEvent(**base)  # type: ignore[arg-type]


def test_event_with_all_fields_passes() -> None:
    ev = _event(
        resource_id="net_1",
        actor="ops",
        http_status=202,
        request_id="req-1",
        payload={"name": "prod"},
    )
    assert ev.actor == "ops"


def test_event_rejects_action_without_dot() -> None:
    with pytest.raises(ValidationError):
        _event(action="bare_action")


def test_event_rejects_empty_resource_type() -> None:
    with pytest.raises(ValidationError):
        _event(resource_type="")


def test_event_payload_defaults_to_empty_dict() -> None:
    ev = _event()
    assert ev.payload == {}
