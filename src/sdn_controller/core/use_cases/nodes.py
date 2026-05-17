"""Node use cases.

For Milestone 2 we add admin-facing lifecycle (``register``, ``remove``) and
make reads apply the *derived* status so callers see ``stale``/``offline``
without a background reaper. Agent-side flows (``enroll``, ``heartbeat``)
live in ``sdn_controller.core.use_cases.enrollment``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sdn_controller.core.entities import Node, Operation, ResourceRef
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.services.node_status import apply_derived_status
from sdn_controller.core.value_objects.enums import (
    NodeStatus,
    OperationKind,
    OperationStatus,
)
from sdn_controller.core.value_objects.errors import ConflictError, NotFoundError
from sdn_controller.core.value_objects.ids import IdFactory, NodeId
from sdn_controller.ports.persistence import NodeRepository, OperationRepository

# ---------------------------------------------------------------------------
# Commands / results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegisterNodeCommand:
    name: str
    mgmt_ip: str
    roles: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class NodeRegistered:
    node: Node
    operation: Operation


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


class RegisterNode:
    """Create a ``pending`` node entry.

    This is the first half of the enrolment flow — it records operator intent
    so a token can be issued and so the controller will accept a heartbeat
    later. The agent transitions the node to ``online`` via ``EnrollAgent``.
    """

    def __init__(
        self,
        *,
        nodes: NodeRepository,
        operations: OperationRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._nodes = nodes
        self._operations = operations
        self._clock = clock
        self._ids = ids

    async def execute(self, cmd: RegisterNodeCommand) -> NodeRegistered:
        existing = await self._nodes.get_by_name(cmd.name)
        if existing is not None:
            raise ConflictError(f"node with name {cmd.name!r} already exists")

        now = self._clock.now()
        node_id = self._ids.node()
        node = Node(
            id=node_id,
            name=cmd.name,
            mgmt_ip=cmd.mgmt_ip,
            status=NodeStatus.PENDING,
            roles=list(cmd.roles),
            labels=dict(cmd.labels),
            created_at=now,
            updated_at=now,
        )

        operation = Operation.accept(
            operation_id=self._ids.operation(),
            kind=OperationKind.NODE_ENROLL,
            resource=ResourceRef(type="node", id=node_id),
            now=now,
            created_by=cmd.created_by,
            message=f"register node {cmd.name!r}",
        )
        for status, message in (
            (OperationStatus.PLANNING, "validating node intent"),
            (OperationStatus.RUNNING, "persisting node record"),
            (OperationStatus.VERIFYING, "verifying persisted record"),
            (OperationStatus.SUCCEEDED, "node registered (pending agent enrolment)"),
        ):
            operation.transition_to(status, now=self._clock.now(), message=message)

        await self._nodes.save(node)
        await self._operations.save(operation)
        return NodeRegistered(node=node, operation=operation)


class RemoveNode:
    """Delete a node (and cascading enrolment tokens) from the controller."""

    def __init__(
        self,
        *,
        nodes: NodeRepository,
        operations: OperationRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._nodes = nodes
        self._operations = operations
        self._clock = clock
        self._ids = ids

    async def execute(self, node_id: NodeId, *, removed_by: str | None = None) -> Operation:
        node = await self._nodes.get(node_id)
        if node is None:
            raise NotFoundError(f"node {node_id} not found")

        now = self._clock.now()
        operation = Operation.accept(
            operation_id=self._ids.operation(),
            kind=OperationKind.NODE_REMOVE,
            resource=ResourceRef(type="node", id=node_id),
            now=now,
            created_by=removed_by,
            message=f"remove node {node.name!r}",
        )
        for status, message in (
            (OperationStatus.PLANNING, "preparing node removal"),
            (OperationStatus.RUNNING, "deleting node record"),
            (OperationStatus.VERIFYING, "verifying deletion"),
            (OperationStatus.SUCCEEDED, "node removed"),
        ):
            operation.transition_to(status, now=self._clock.now(), message=message)

        await self._nodes.delete(node_id)
        await self._operations.save(operation)
        return operation


# ---------------------------------------------------------------------------
# Reads (apply derived status)
# ---------------------------------------------------------------------------


class ListNodes:
    """List nodes with derived status (online/stale/offline) applied."""

    def __init__(
        self,
        *,
        nodes: NodeRepository,
        clock: Clock,
        stale_after_seconds: int,
        offline_after_seconds: int,
    ) -> None:
        self._nodes = nodes
        self._clock = clock
        self._stale_after_seconds = stale_after_seconds
        self._offline_after_seconds = offline_after_seconds

    async def execute(self) -> list[Node]:
        now = self._clock.now()
        items = await self._nodes.list()
        for n in items:
            apply_derived_status(
                n,
                now=now,
                stale_after_seconds=self._stale_after_seconds,
                offline_after_seconds=self._offline_after_seconds,
            )
        return items


class GetNode:
    def __init__(
        self,
        *,
        nodes: NodeRepository,
        clock: Clock,
        stale_after_seconds: int,
        offline_after_seconds: int,
    ) -> None:
        self._nodes = nodes
        self._clock = clock
        self._stale_after_seconds = stale_after_seconds
        self._offline_after_seconds = offline_after_seconds

    async def execute(self, node_id: NodeId) -> Node:
        node = await self._nodes.get(node_id)
        if node is None:
            raise NotFoundError(f"node {node_id} not found")
        apply_derived_status(
            node,
            now=self._clock.now(),
            stale_after_seconds=self._stale_after_seconds,
            offline_after_seconds=self._offline_after_seconds,
        )
        return node
