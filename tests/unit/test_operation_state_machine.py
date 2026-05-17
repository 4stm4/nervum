"""Operation aggregate: state machine and event log."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdn_controller.core.entities import (
    Operation,
    OperationError,
    ResourceRef,
)
from sdn_controller.core.value_objects.enums import OperationKind, OperationStatus
from sdn_controller.core.value_objects.errors import InvalidStateTransition
from sdn_controller.core.value_objects.ids import OperationId


def _make_operation(now: datetime) -> Operation:
    return Operation.accept(
        operation_id=OperationId("op_1"),
        kind=OperationKind.NETWORK_CREATE,
        resource=ResourceRef(type="network", id="net_1"),
        now=now,
        created_by="tester",
    )


def test_accept_initialises_with_one_event() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)

    op = _make_operation(now)

    assert op.status is OperationStatus.ACCEPTED
    assert op.created_at == now
    assert op.updated_at == now
    assert len(op.events) == 1
    assert op.events[0].sequence == 1
    assert op.events[0].status is OperationStatus.ACCEPTED
    assert op.is_terminal is False


def test_full_happy_path_records_each_transition() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    op = _make_operation(now)

    for status in (
        OperationStatus.PLANNING,
        OperationStatus.RUNNING,
        OperationStatus.VERIFYING,
        OperationStatus.SUCCEEDED,
    ):
        op.transition_to(status, now=now, message=f"-> {status.value}")

    assert op.status is OperationStatus.SUCCEEDED
    assert op.is_terminal is True
    sequences = [e.sequence for e in op.events]
    assert sequences == [1, 2, 3, 4, 5]
    statuses = [e.status for e in op.events]
    assert statuses == [
        OperationStatus.ACCEPTED,
        OperationStatus.PLANNING,
        OperationStatus.RUNNING,
        OperationStatus.VERIFYING,
        OperationStatus.SUCCEEDED,
    ]


def test_cannot_skip_states() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    op = _make_operation(now)

    with pytest.raises(InvalidStateTransition):
        op.transition_to(OperationStatus.SUCCEEDED, now=now, message="skip")


def test_terminal_state_is_final() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    op = _make_operation(now)
    op.transition_to(OperationStatus.PLANNING, now=now, message="p")
    op.transition_to(OperationStatus.RUNNING, now=now, message="r")
    op.transition_to(OperationStatus.VERIFYING, now=now, message="v")
    op.transition_to(OperationStatus.SUCCEEDED, now=now, message="s")

    with pytest.raises(InvalidStateTransition):
        op.transition_to(OperationStatus.RUNNING, now=now, message="resurrect")


def test_failed_records_error() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    op = _make_operation(now)
    op.transition_to(OperationStatus.PLANNING, now=now, message="p")
    op.transition_to(OperationStatus.RUNNING, now=now, message="r")

    op.transition_to(
        OperationStatus.FAILED,
        now=now,
        message="agent unreachable",
        error=OperationError(code="agent_unreachable", message="node-1 offline"),
    )

    assert op.status is OperationStatus.FAILED
    assert op.is_terminal is True
    assert op.error is not None
    assert op.error.code == "agent_unreachable"


def test_error_rejected_outside_failure_states() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    op = _make_operation(now)

    with pytest.raises(InvalidStateTransition):
        op.transition_to(
            OperationStatus.PLANNING,
            now=now,
            message="bad",
            error=OperationError(code="x", message="x"),
        )


def test_cancel_from_accepted() -> None:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    op = _make_operation(now)

    op.transition_to(OperationStatus.CANCELLED, now=now, message="user cancelled")

    assert op.status is OperationStatus.CANCELLED
    assert op.is_terminal is True
