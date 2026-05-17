"""In-memory repositories.

These implementations are deliberately simple and **not** designed for
production durability — they exist so that:

* the core can be exercised end-to-end without a database,
* the PostgreSQL adapter (Milestone 1 / SDN-002) has a behavioural reference,
* tests run without a network round-trip.

A single ``anyio.Lock`` per repository is enough: FastAPI handlers run on the
same event loop, so we just need atomicity between awaits, not real isolation.
"""

from __future__ import annotations

import copy

import anyio

from sdn_controller.core.entities import Network, Node, Operation, OperationEvent
from sdn_controller.core.value_objects.enums import OperationStatus
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import NetworkId, NodeId, OperationId


class InMemoryNodeRepository:
    def __init__(self) -> None:
        self._items: dict[NodeId, Node] = {}
        self._lock = anyio.Lock()

    async def get(self, node_id: NodeId) -> Node | None:
        async with self._lock:
            node = self._items.get(node_id)
            return copy.deepcopy(node) if node is not None else None

    async def list(self) -> list[Node]:
        async with self._lock:
            return [copy.deepcopy(n) for n in self._items.values()]

    async def save(self, node: Node) -> None:
        async with self._lock:
            self._items[node.id] = copy.deepcopy(node)


class InMemoryNetworkRepository:
    def __init__(self) -> None:
        self._items: dict[NetworkId, Network] = {}
        self._lock = anyio.Lock()

    async def get(self, network_id: NetworkId) -> Network | None:
        async with self._lock:
            net = self._items.get(network_id)
            return copy.deepcopy(net) if net is not None else None

    async def get_by_name(self, name: str) -> Network | None:
        async with self._lock:
            for net in self._items.values():
                if net.name == name:
                    return copy.deepcopy(net)
            return None

    async def list(self) -> list[Network]:
        async with self._lock:
            return [copy.deepcopy(n) for n in self._items.values()]

    async def save(self, network: Network) -> None:
        async with self._lock:
            self._items[network.id] = copy.deepcopy(network)

    async def delete(self, network_id: NetworkId) -> None:
        async with self._lock:
            self._items.pop(network_id, None)


class InMemoryOperationRepository:
    def __init__(self) -> None:
        self._items: dict[OperationId, Operation] = {}
        self._lock = anyio.Lock()

    async def get(self, operation_id: OperationId) -> Operation | None:
        async with self._lock:
            op = self._items.get(operation_id)
            return copy.deepcopy(op) if op is not None else None

    async def list(self, *, limit: int = 100) -> list[Operation]:
        async with self._lock:
            ops = sorted(self._items.values(), key=lambda o: o.created_at, reverse=True)
            return [copy.deepcopy(o) for o in ops[:limit]]

    async def save(self, operation: Operation) -> None:
        async with self._lock:
            self._items[operation.id] = copy.deepcopy(operation)

    async def update_status(
        self,
        operation_id: OperationId,
        status: OperationStatus,
        event: OperationEvent,
    ) -> None:
        async with self._lock:
            op = self._items.get(operation_id)
            if op is None:
                raise NotFoundError(f"operation {operation_id} not found")
            op.status = status
            op.updated_at = event.at
            op.events.append(event)
