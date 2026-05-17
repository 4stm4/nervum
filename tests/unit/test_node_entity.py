"""``Node`` lifecycle transitions and derived-status service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sdn_controller.core.entities import Node
from sdn_controller.core.services.node_status import derived_status
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.enums import NodeStatus
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import NodeId

_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def _node(**overrides: object) -> Node:
    base: dict[str, object] = {
        "id": NodeId("node_1"),
        "name": "edge-1",
        "mgmt_ip": "10.0.0.10",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return Node(**base)  # type: ignore[arg-type]


def test_enroll_transitions_pending_to_online() -> None:
    node = _node()
    caps = NodeCapabilities(ovs_version="3.2.1", interfaces=("eth0",))

    node.enroll(now=_NOW + timedelta(seconds=10), agent_version="0.1.0", capabilities=caps)

    assert node.status is NodeStatus.ONLINE
    assert node.agent_version == "0.1.0"
    assert node.last_seen_at == _NOW + timedelta(seconds=10)
    assert node.capabilities == caps


def test_enroll_rejected_for_non_pending() -> None:
    node = _node(status=NodeStatus.ONLINE, last_seen_at=_NOW)

    with pytest.raises(ValidationError, match="not pending"):
        node.enroll(now=_NOW)


def test_heartbeat_refuses_pending_node() -> None:
    node = _node()  # pending

    with pytest.raises(ValidationError, match="still pending"):
        node.record_heartbeat(now=_NOW)


def test_heartbeat_recovers_offline_to_online() -> None:
    node = _node(status=NodeStatus.OFFLINE, last_seen_at=_NOW - timedelta(hours=1))

    node.record_heartbeat(now=_NOW, agent_version="0.2.0")

    assert node.status is NodeStatus.ONLINE
    assert node.last_seen_at == _NOW
    assert node.agent_version == "0.2.0"


def test_derived_status_online_within_stale_window() -> None:
    node = _node(status=NodeStatus.ONLINE, last_seen_at=_NOW - timedelta(seconds=30))

    s = derived_status(node, now=_NOW, stale_after_seconds=90, offline_after_seconds=300)

    assert s is NodeStatus.ONLINE


def test_derived_status_flips_to_stale_then_offline() -> None:
    node = _node(status=NodeStatus.ONLINE, last_seen_at=_NOW - timedelta(seconds=120))

    s = derived_status(node, now=_NOW, stale_after_seconds=90, offline_after_seconds=300)

    assert s is NodeStatus.STALE

    node.last_seen_at = _NOW - timedelta(seconds=600)
    assert (
        derived_status(node, now=_NOW, stale_after_seconds=90, offline_after_seconds=300)
        is NodeStatus.OFFLINE
    )


def test_derived_status_preserves_pending_and_draining() -> None:
    pending = _node(status=NodeStatus.PENDING, last_seen_at=None)
    draining = _node(status=NodeStatus.DRAINING, last_seen_at=_NOW - timedelta(hours=1))

    assert (
        derived_status(pending, now=_NOW, stale_after_seconds=90, offline_after_seconds=300)
        is NodeStatus.PENDING
    )
    assert (
        derived_status(draining, now=_NOW, stale_after_seconds=90, offline_after_seconds=300)
        is NodeStatus.DRAINING
    )
