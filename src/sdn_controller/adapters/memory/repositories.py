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
from datetime import datetime

import anyio

from sdn_controller.core.entities import (
    AuditEvent,
    EnrollmentToken,
    IpAllocation,
    Network,
    Node,
    NodeSnapshot,
    ObservedState,
    Operation,
    OperationEvent,
    ServiceAccount,
    ServiceToken,
)
from sdn_controller.core.value_objects.enums import OperationStatus
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import (
    AuditEventId,
    EnrollmentTokenId,
    IpAllocationId,
    NetworkId,
    NodeId,
    NodeSnapshotId,
    OperationId,
    ServiceAccountId,
    ServiceTokenId,
    SubnetId,
)
from sdn_controller.core.value_objects.ipam import OwnerRef


class InMemoryNodeRepository:
    def __init__(self) -> None:
        self._items: dict[NodeId, Node] = {}
        self._lock = anyio.Lock()

    async def get(self, node_id: NodeId) -> Node | None:
        async with self._lock:
            node = self._items.get(node_id)
            return copy.deepcopy(node) if node is not None else None

    async def get_by_name(self, name: str) -> Node | None:
        async with self._lock:
            for node in self._items.values():
                if node.name == name:
                    return copy.deepcopy(node)
            return None

    async def list(self) -> list[Node]:
        async with self._lock:
            return [copy.deepcopy(n) for n in self._items.values()]

    async def save(self, node: Node) -> None:
        async with self._lock:
            self._items[node.id] = copy.deepcopy(node)

    async def delete(self, node_id: NodeId) -> None:
        async with self._lock:
            self._items.pop(node_id, None)


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

    async def get_by_subnet_id(self, subnet_id: SubnetId) -> Network | None:
        async with self._lock:
            for net in self._items.values():
                if net.subnet is not None and net.subnet.id == subnet_id:
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


class InMemoryEnrollmentTokenRepository:
    def __init__(self) -> None:
        self._items: dict[EnrollmentTokenId, EnrollmentToken] = {}
        self._lock = anyio.Lock()

    async def get(self, token_id: EnrollmentTokenId) -> EnrollmentToken | None:
        async with self._lock:
            tok = self._items.get(token_id)
            return copy.deepcopy(tok) if tok is not None else None

    async def get_by_hash(self, token_hash: str) -> EnrollmentToken | None:
        async with self._lock:
            for tok in self._items.values():
                if tok.token_hash == token_hash:
                    return copy.deepcopy(tok)
            return None

    async def list_for_node(self, node_id: NodeId) -> list[EnrollmentToken]:
        async with self._lock:
            return [copy.deepcopy(t) for t in self._items.values() if t.node_id == node_id]

    async def save(self, token: EnrollmentToken) -> None:
        async with self._lock:
            self._items[token.id] = copy.deepcopy(token)

    async def delete_for_node(self, node_id: NodeId) -> None:
        async with self._lock:
            for tid in [t.id for t in self._items.values() if t.node_id == node_id]:
                self._items.pop(tid, None)


class InMemoryObservedStateRepository:
    def __init__(self) -> None:
        self._items: dict[NodeId, ObservedState] = {}
        self._lock = anyio.Lock()

    async def get(self, node_id: NodeId) -> ObservedState | None:
        async with self._lock:
            state = self._items.get(node_id)
            return copy.deepcopy(state) if state is not None else None

    async def save(self, state: ObservedState) -> None:
        async with self._lock:
            self._items[state.node_id] = copy.deepcopy(state)

    async def delete(self, node_id: NodeId) -> None:
        async with self._lock:
            self._items.pop(node_id, None)


class InMemoryIpAllocationRepository:
    def __init__(self) -> None:
        self._items: dict[IpAllocationId, IpAllocation] = {}
        self._lock = anyio.Lock()

    async def get(self, allocation_id: IpAllocationId) -> IpAllocation | None:
        async with self._lock:
            alloc = self._items.get(allocation_id)
            return copy.deepcopy(alloc) if alloc is not None else None

    async def get_by_address(self, subnet_id: SubnetId, address: str) -> IpAllocation | None:
        async with self._lock:
            for alloc in self._items.values():
                if alloc.subnet_id == subnet_id and alloc.ip_address == address:
                    return copy.deepcopy(alloc)
            return None

    async def list_for_subnet(self, subnet_id: SubnetId) -> list[IpAllocation]:
        async with self._lock:
            return [copy.deepcopy(a) for a in self._items.values() if a.subnet_id == subnet_id]

    async def list_for_owner(self, owner: OwnerRef) -> list[IpAllocation]:
        async with self._lock:
            return [copy.deepcopy(a) for a in self._items.values() if a.owner == owner]

    async def save(self, allocation: IpAllocation) -> None:
        async with self._lock:
            self._items[allocation.id] = copy.deepcopy(allocation)

    async def delete(self, allocation_id: IpAllocationId) -> None:
        async with self._lock:
            self._items.pop(allocation_id, None)


class InMemoryServiceAccountRepository:
    def __init__(self) -> None:
        self._items: dict[ServiceAccountId, ServiceAccount] = {}
        self._lock = anyio.Lock()

    async def get(self, account_id: ServiceAccountId) -> ServiceAccount | None:
        async with self._lock:
            sa = self._items.get(account_id)
            return copy.deepcopy(sa) if sa is not None else None

    async def get_by_name(self, name: str) -> ServiceAccount | None:
        async with self._lock:
            for sa in self._items.values():
                if sa.name == name:
                    return copy.deepcopy(sa)
            return None

    async def list(self) -> list[ServiceAccount]:
        async with self._lock:
            return [copy.deepcopy(sa) for sa in self._items.values()]

    async def save(self, account: ServiceAccount) -> None:
        async with self._lock:
            self._items[account.id] = copy.deepcopy(account)


class InMemoryServiceTokenRepository:
    def __init__(self) -> None:
        self._items: dict[ServiceTokenId, ServiceToken] = {}
        self._lock = anyio.Lock()

    async def get(self, token_id: ServiceTokenId) -> ServiceToken | None:
        async with self._lock:
            tok = self._items.get(token_id)
            return copy.deepcopy(tok) if tok is not None else None

    async def get_by_hash(self, token_hash: str) -> ServiceToken | None:
        async with self._lock:
            for tok in self._items.values():
                if tok.token_hash == token_hash:
                    return copy.deepcopy(tok)
            return None

    async def list_for_account(self, account_id: ServiceAccountId) -> list[ServiceToken]:
        async with self._lock:
            return [
                copy.deepcopy(t) for t in self._items.values() if t.service_account_id == account_id
            ]

    async def save(self, token: ServiceToken) -> None:
        async with self._lock:
            self._items[token.id] = copy.deepcopy(token)


class InMemoryAuditEventRepository:
    def __init__(self) -> None:
        self._items: dict[AuditEventId, AuditEvent] = {}
        self._lock = anyio.Lock()

    async def save(self, event: AuditEvent) -> None:
        async with self._lock:
            self._items[event.id] = copy.deepcopy(event)

    async def get(self, event_id: AuditEventId) -> AuditEvent | None:
        async with self._lock:
            ev = self._items.get(event_id)
            return copy.deepcopy(ev) if ev is not None else None

    async def list(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        async with self._lock:
            items = list(self._items.values())
        items.sort(key=lambda e: e.at, reverse=True)
        out: list[AuditEvent] = []
        for ev in items:
            if actor is not None and ev.actor != actor:
                continue
            if action is not None and ev.action != action:
                continue
            if resource_type is not None and ev.resource_type != resource_type:
                continue
            if resource_id is not None and ev.resource_id != resource_id:
                continue
            if since is not None and ev.at < since:
                continue
            out.append(copy.deepcopy(ev))
            if len(out) >= limit:
                break
        return out


class InMemoryNodeSnapshotRepository:
    def __init__(self) -> None:
        self._items: dict[NodeSnapshotId, NodeSnapshot] = {}
        self._lock = anyio.Lock()

    async def save(self, snapshot: NodeSnapshot) -> None:
        async with self._lock:
            self._items[snapshot.id] = copy.deepcopy(snapshot)

    async def get(self, snapshot_id: NodeSnapshotId) -> NodeSnapshot | None:
        async with self._lock:
            snap = self._items.get(snapshot_id)
            return copy.deepcopy(snap) if snap is not None else None

    async def list_for_node(self, node_id: NodeId) -> list[NodeSnapshot]:
        async with self._lock:
            items = [copy.deepcopy(s) for s in self._items.values() if s.node_id == node_id]
        items.sort(key=lambda s: s.created_at, reverse=True)
        return items

    async def list(self, *, limit: int = 200) -> list[NodeSnapshot]:
        async with self._lock:
            items = [copy.deepcopy(s) for s in self._items.values()]
        items.sort(key=lambda s: s.created_at, reverse=True)
        return items[:limit]

    async def delete(self, snapshot_id: NodeSnapshotId) -> None:
        async with self._lock:
            self._items.pop(snapshot_id, None)
