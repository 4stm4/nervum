"""Operation aggregate.

Every mutating API call produces an ``Operation``. Operations are the only
contract that external systems need to follow progress, errors and rollback.

The aggregate is responsible for:

* enforcing the state machine,
* appending an immutable event for every transition,
* recording terminal results (error or success).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sdn_controller.core.value_objects.enums import OperationKind, OperationStatus
from sdn_controller.core.value_objects.errors import InvalidStateTransition
from sdn_controller.core.value_objects.ids import OperationId


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """A pointer to the aggregate an operation is acting on."""

    type: str
    id: str


@dataclass(frozen=True, slots=True)
class OperationError:
    """Terminal error attached to a failed/rolled_back operation."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OperationEvent:
    """Immutable record of a single state change inside an operation."""

    sequence: int
    at: datetime
    status: OperationStatus
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

_ALLOWED_TRANSITIONS: dict[OperationStatus, frozenset[OperationStatus]] = {
    OperationStatus.ACCEPTED: frozenset(
        {OperationStatus.PLANNING, OperationStatus.CANCELLED, OperationStatus.FAILED}
    ),
    OperationStatus.PLANNING: frozenset(
        {OperationStatus.RUNNING, OperationStatus.CANCELLED, OperationStatus.FAILED}
    ),
    OperationStatus.RUNNING: frozenset(
        {
            OperationStatus.VERIFYING,
            OperationStatus.CANCELLED,
            OperationStatus.FAILED,
            OperationStatus.ROLLED_BACK,
        }
    ),
    OperationStatus.VERIFYING: frozenset(
        {OperationStatus.SUCCEEDED, OperationStatus.FAILED, OperationStatus.ROLLED_BACK}
    ),
    # Terminal states have no outgoing transitions.
    OperationStatus.SUCCEEDED: frozenset(),
    OperationStatus.FAILED: frozenset(),
    OperationStatus.CANCELLED: frozenset(),
    OperationStatus.ROLLED_BACK: frozenset(),
}


@dataclass(slots=True)
class Operation:
    """Long-running, observable unit of work."""

    id: OperationId
    kind: OperationKind
    resource: ResourceRef
    created_at: datetime
    updated_at: datetime
    status: OperationStatus = OperationStatus.ACCEPTED
    created_by: str | None = None
    events: list[OperationEvent] = field(default_factory=list)
    error: OperationError | None = None

    # -- factory -----------------------------------------------------------

    @classmethod
    def accept(
        cls,
        *,
        operation_id: OperationId,
        kind: OperationKind,
        resource: ResourceRef,
        now: datetime,
        created_by: str | None = None,
        message: str = "operation accepted",
    ) -> Operation:
        op = cls(
            id=operation_id,
            kind=kind,
            resource=resource,
            created_at=now,
            updated_at=now,
            status=OperationStatus.ACCEPTED,
            created_by=created_by,
        )
        op.events.append(
            OperationEvent(
                sequence=1,
                at=now,
                status=OperationStatus.ACCEPTED,
                message=message,
            )
        )
        return op

    # -- transitions -------------------------------------------------------

    def transition_to(
        self,
        new_status: OperationStatus,
        *,
        now: datetime,
        message: str,
        payload: dict[str, Any] | None = None,
        error: OperationError | None = None,
    ) -> OperationEvent:
        """Move into ``new_status`` while validating the state machine.

        Raises ``InvalidStateTransition`` if the move is not allowed. Always
        appends an ``OperationEvent`` recording the transition.
        """
        allowed = _ALLOWED_TRANSITIONS[self.status]
        if new_status not in allowed:
            raise InvalidStateTransition(
                f"operation {self.id} cannot move from {self.status.value} to {new_status.value}",
            )

        if error is not None and new_status not in {
            OperationStatus.FAILED,
            OperationStatus.ROLLED_BACK,
        }:
            raise InvalidStateTransition(
                f"error may only be recorded with FAILED or ROLLED_BACK, got {new_status.value}",
            )

        self.status = new_status
        self.updated_at = now
        if error is not None:
            self.error = error

        event = OperationEvent(
            sequence=len(self.events) + 1,
            at=now,
            status=new_status,
            message=message,
            payload=payload or {},
        )
        self.events.append(event)
        return event

    # -- queries -----------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal
