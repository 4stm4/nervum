"""Инварианты ``Node.tls_thumbprint`` и enrollment thumbprint flow."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdn_controller.core.entities import Node
from sdn_controller.core.value_objects.enums import NodeStatus
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import NodeId

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
_VALID = "a" * 64  # 64 hex char placeholder
_INVALID_SHORT = "ab" * 31
_INVALID_NONHEX = "z" * 64


def _node(**overrides: object) -> Node:
    return Node(
        id=NodeId("node_1"),
        name="n",
        mgmt_ip="10.0.0.1",
        created_at=_NOW,
        updated_at=_NOW,
        **overrides,  # type: ignore[arg-type]
    )


def test_node_accepts_valid_thumbprint() -> None:
    node = _node(tls_thumbprint=_VALID)
    assert node.tls_thumbprint == _VALID


def test_node_normalizes_thumbprint_case() -> None:
    node = _node(tls_thumbprint=_VALID.upper())
    assert node.tls_thumbprint == _VALID  # lowercased


def test_node_rejects_short_thumbprint() -> None:
    with pytest.raises(ValidationError):
        _node(tls_thumbprint=_INVALID_SHORT)


def test_node_rejects_non_hex_thumbprint() -> None:
    with pytest.raises(ValidationError):
        _node(tls_thumbprint=_INVALID_NONHEX)


def test_enroll_records_thumbprint() -> None:
    node = _node()
    node.enroll(now=_NOW, tls_thumbprint=_VALID)
    assert node.status is NodeStatus.ONLINE
    assert node.tls_thumbprint == _VALID


def test_enroll_with_bad_thumbprint_raises_and_keeps_pending() -> None:
    node = _node()
    with pytest.raises(ValidationError):
        node.enroll(now=_NOW, tls_thumbprint=_INVALID_SHORT)
    assert node.status is NodeStatus.PENDING
    assert node.tls_thumbprint is None
