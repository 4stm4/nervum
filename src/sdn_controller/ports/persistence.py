"""Persistence ports.

We expose **one repository per aggregate**. Each repository hides storage
specifics — the same protocol is satisfied by the in-memory adapter (used in
tests and the MVP) and by the SQLAlchemy adapter (Milestone 1 / SDN-002).
"""

from __future__ import annotations

from typing import Protocol

from sdn_controller.core.entities import Network, Node, Operation, OperationEvent
from sdn_controller.core.value_objects.enums import OperationStatus
from sdn_controller.core.value_objects.ids import NetworkId, NodeId, OperationId


class NodeRepository(Protocol):
    async def get(self, node_id: NodeId) -> Node | None: ...
    async def list(self) -> list[Node]: ...
    async def save(self, node: Node) -> None: ...


class NetworkRepository(Protocol):
    async def get(self, network_id: NetworkId) -> Network | None: ...
    async def get_by_name(self, name: str) -> Network | None: ...
    async def list(self) -> list[Network]: ...
    async def save(self, network: Network) -> None: ...
    async def delete(self, network_id: NetworkId) -> None: ...


class OperationRepository(Protocol):
    async def get(self, operation_id: OperationId) -> Operation | None: ...
    async def list(self, *, limit: int = 100) -> list[Operation]: ...
    async def save(self, operation: Operation) -> None: ...
    async def update_status(
        self,
        operation_id: OperationId,
        status: OperationStatus,
        event: OperationEvent,
    ) -> None: ...
