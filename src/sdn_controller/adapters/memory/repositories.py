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
from collections.abc import Sequence
from datetime import datetime

import anyio

from sdn_controller.core.entities import (
    AddressPool,
    AuditEvent,
    BgpPeer,
    EnrollmentToken,
    FloatingIP,
    GatewayBond,
    HealthMonitor,
    IpAllocation,
    LbListener,
    LbMember,
    LbPool,
    LoadBalancer,
    LogicalPort,
    Network,
    Node,
    NodeSnapshot,
    ObservedState,
    Operation,
    OperationEvent,
    OutboxEvent,
    Project,
    ProjectMember,
    ProjectQuota,
    QosPolicy,
    ResourceSnapshot,
    RetentionPolicy,
    Router,
    SecurityGroup,
    SecurityGroupMember,
    SecurityPolicy,
    ServiceAccount,
    ServiceObject,
    ServiceToken,
    TrunkPort,
    WebhookSubscription,
)
from sdn_controller.core.value_objects.enums import (
    OperationStatus,
    RetentionScope,
    WebhookSubscriptionState,
)
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import (
    AddressPoolId,
    AuditEventId,
    BgpPeerId,
    EnrollmentTokenId,
    FloatingIpId,
    GatewayBondId,
    HealthMonitorId,
    IpAllocationId,
    LbListenerId,
    LbMemberId,
    LbPoolId,
    LoadBalancerId,
    LogicalPortId,
    NetworkId,
    NodeId,
    NodeSnapshotId,
    OperationId,
    OutboxEventId,
    ProjectId,
    ProjectQuotaId,
    QosPolicyId,
    ResourceSnapshotId,
    RetentionPolicyId,
    RouterId,
    SecurityGroupId,
    SecurityPolicyId,
    ServiceAccountId,
    ServiceObjectId,
    ServiceTokenId,
    SubnetId,
    TrunkPortId,
    WebhookSubscriptionId,
)
from sdn_controller.core.value_objects.ipam import OwnerRef
from sdn_controller.core.value_objects.security import Role


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

    async def delete_terminal_before(self, cutoff: datetime) -> int:
        """Удалить терминальные operations старше ``cutoff``. Возвращает
        количество удалённых записей."""
        terminal = {
            OperationStatus.SUCCEEDED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
            OperationStatus.ROLLED_BACK,
        }
        async with self._lock:
            victims = [
                op_id
                for op_id, op in self._items.items()
                if op.status in terminal and op.updated_at < cutoff
            ]
            for op_id in victims:
                self._items.pop(op_id, None)
        return len(victims)


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

    async def list_before(self, cutoff: datetime, *, limit: int = 1000) -> Sequence[AuditEvent]:
        async with self._lock:
            items = sorted(
                (ev for ev in self._items.values() if ev.at < cutoff),
                key=lambda e: e.at,
            )
            return [copy.deepcopy(e) for e in items[:limit]]

    async def delete_before(self, cutoff: datetime) -> int:
        async with self._lock:
            victims = [eid for eid, ev in self._items.items() if ev.at < cutoff]
            for eid in victims:
                self._items.pop(eid, None)
        return len(victims)

    async def delete_many(self, event_ids: Sequence[AuditEventId]) -> int:
        if not event_ids:
            return 0
        async with self._lock:
            removed = 0
            for eid in event_ids:
                if self._items.pop(eid, None) is not None:
                    removed += 1
        return removed


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


class InMemoryOutboxRepository:
    """Monotonic event sequence in memory.

    Помимо ``id`` (Stripe-style строкой) держим автоинкрементный
    ``_seq`` — это и есть ``event_id``, отдаваемый подписчикам как
    watermark. ``append`` инкрементирует счётчик под мьютексом.
    """

    def __init__(self) -> None:
        self._items: dict[OutboxEventId, OutboxEvent] = {}
        self._seq: int = 0
        self._lock = anyio.Lock()

    async def append(self, event: OutboxEvent) -> OutboxEvent:
        async with self._lock:
            self._seq += 1
            stored = OutboxEvent(
                id=event.id,
                event_id=self._seq,
                occurred_at=event.occurred_at,
                event_type=event.event_type,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                payload=dict(event.payload),
                delivered_at=event.delivered_at,
                schema_version=event.schema_version,
                project_id=event.project_id,
            )
            self._items[stored.id] = stored
            return copy.deepcopy(stored)

    async def get(self, event_id: OutboxEventId) -> OutboxEvent | None:
        async with self._lock:
            item = self._items.get(event_id)
            return copy.deepcopy(item) if item is not None else None

    async def list_since(self, *, since: int = 0, limit: int = 200) -> Sequence[OutboxEvent]:
        async with self._lock:
            items = [copy.deepcopy(e) for e in self._items.values() if e.event_id > since]
        items.sort(key=lambda e: e.event_id)
        return items[:limit]

    async def list_undelivered(self, *, limit: int = 200) -> Sequence[OutboxEvent]:
        async with self._lock:
            items = [copy.deepcopy(e) for e in self._items.values() if e.delivered_at is None]
        items.sort(key=lambda e: e.event_id)
        return items[:limit]

    async def mark_delivered(self, event_ids: Sequence[OutboxEventId], *, at: datetime) -> None:
        async with self._lock:
            for oid in event_ids:
                current = self._items.get(oid)
                if current is None or current.delivered_at is not None:
                    continue
                self._items[oid] = OutboxEvent(
                    id=current.id,
                    event_id=current.event_id,
                    occurred_at=current.occurred_at,
                    event_type=current.event_type,
                    resource_type=current.resource_type,
                    resource_id=current.resource_id,
                    payload=dict(current.payload),
                    delivered_at=at,
                    schema_version=current.schema_version,
                    project_id=current.project_id,
                )

    async def head_event_id(self) -> int:
        async with self._lock:
            return self._seq

    async def delete_delivered_before(self, cutoff: datetime) -> int:
        async with self._lock:
            victims = [
                oid
                for oid, ev in self._items.items()
                if ev.delivered_at is not None and ev.delivered_at < cutoff
            ]
            for oid in victims:
                self._items.pop(oid, None)
            return len(victims)


class InMemoryWebhookSubscriptionRepository:
    def __init__(self) -> None:
        self._items: dict[WebhookSubscriptionId, WebhookSubscription] = {}
        self._lock = anyio.Lock()

    async def save(self, subscription: WebhookSubscription) -> None:
        async with self._lock:
            self._items[subscription.id] = copy.deepcopy(subscription)

    async def get(self, sub_id: WebhookSubscriptionId) -> WebhookSubscription | None:
        async with self._lock:
            item = self._items.get(sub_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self) -> Sequence[WebhookSubscription]:
        async with self._lock:
            items = [copy.deepcopy(s) for s in self._items.values()]
        items.sort(key=lambda s: s.created_at)
        return items

    async def list_active(self) -> Sequence[WebhookSubscription]:
        async with self._lock:
            items = [
                copy.deepcopy(s)
                for s in self._items.values()
                if s.state is WebhookSubscriptionState.ACTIVE
            ]
        items.sort(key=lambda s: s.created_at)
        return items

    async def delete(self, sub_id: WebhookSubscriptionId) -> None:
        async with self._lock:
            self._items.pop(sub_id, None)


class InMemoryProjectRepository:
    def __init__(self) -> None:
        self._items: dict[ProjectId, Project] = {}
        self._lock = anyio.Lock()

    async def get(self, project_id: ProjectId) -> Project | None:
        async with self._lock:
            item = self._items.get(project_id)
            return copy.deepcopy(item) if item is not None else None

    async def get_by_slug(self, slug: str) -> Project | None:
        async with self._lock:
            for proj in self._items.values():
                if proj.slug == slug:
                    return copy.deepcopy(proj)
            return None

    async def list(self) -> list[Project]:
        async with self._lock:
            items = sorted(self._items.values(), key=lambda p: p.created_at)
            return [copy.deepcopy(p) for p in items]

    async def save(self, project: Project) -> None:
        async with self._lock:
            self._items[project.id] = copy.deepcopy(project)

    async def delete(self, project_id: ProjectId) -> None:
        async with self._lock:
            self._items.pop(project_id, None)


class InMemoryProjectMemberRepository:
    def __init__(self) -> None:
        # key = (project_id, sa_id)
        self._items: dict[tuple[ProjectId, ServiceAccountId], ProjectMember] = {}
        self._lock = anyio.Lock()

    async def get(
        self, project_id: ProjectId, sa_id: ServiceAccountId
    ) -> ProjectMember | None:
        async with self._lock:
            item = self._items.get((project_id, sa_id))
            return copy.deepcopy(item) if item is not None else None

    async def list_for_project(self, project_id: ProjectId) -> list[ProjectMember]:
        async with self._lock:
            items = [
                copy.deepcopy(m)
                for k, m in self._items.items()
                if k[0] == project_id
            ]
        items.sort(key=lambda m: m.created_at)
        return items

    async def list_for_account(self, sa_id: ServiceAccountId) -> list[ProjectMember]:
        async with self._lock:
            items = [
                copy.deepcopy(m)
                for k, m in self._items.items()
                if k[1] == sa_id
            ]
        items.sort(key=lambda m: m.created_at)
        return items

    async def save(self, member: ProjectMember) -> None:
        async with self._lock:
            self._items[(member.project_id, member.service_account_id)] = copy.deepcopy(member)

    async def delete(self, project_id: ProjectId, sa_id: ServiceAccountId) -> None:
        async with self._lock:
            self._items.pop((project_id, sa_id), None)

    async def has_role(
        self, project_id: ProjectId, sa_id: ServiceAccountId, role: Role
    ) -> bool:
        member = await self.get(project_id, sa_id)
        if member is None:
            return False
        if member.role == Role.ADMIN:
            return True
        return member.role == role


# ---------------------------------------------------------------------------
# N1 in-memory repositories
# ---------------------------------------------------------------------------


class InMemoryLogicalPortRepository:
    def __init__(self) -> None:
        self._items: dict[LogicalPortId, LogicalPort] = {}
        self._lock = anyio.Lock()

    async def get(self, port_id: LogicalPortId) -> LogicalPort | None:
        async with self._lock:
            item = self._items.get(port_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(
        self,
        *,
        node_id: NodeId | None = None,
        network_id: NetworkId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[LogicalPort]:
        async with self._lock:
            items = list(self._items.values())
        if node_id is not None:
            items = [p for p in items if p.node_id == node_id]
        if network_id is not None:
            items = [p for p in items if p.network_id == network_id]
        if project_id is not None:
            items = [p for p in items if p.project_id == project_id]
        items.sort(key=lambda p: p.created_at)
        return [copy.deepcopy(p) for p in items]

    async def save(self, port: LogicalPort) -> None:
        async with self._lock:
            self._items[port.id] = copy.deepcopy(port)

    async def delete(self, port_id: LogicalPortId) -> None:
        async with self._lock:
            self._items.pop(port_id, None)

    async def delete_for_node(self, node_id: NodeId) -> None:
        async with self._lock:
            to_delete = [pid for pid, p in self._items.items() if p.node_id == node_id]
            for pid in to_delete:
                del self._items[pid]


class InMemorySecurityGroupRepository:
    def __init__(self) -> None:
        self._items: dict[SecurityGroupId, SecurityGroup] = {}
        self._lock = anyio.Lock()

    async def get(self, sg_id: SecurityGroupId) -> SecurityGroup | None:
        async with self._lock:
            item = self._items.get(sg_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[SecurityGroup]:
        async with self._lock:
            items = list(self._items.values())
        if project_id is not None:
            items = [sg for sg in items if sg.project_id == project_id]
        items.sort(key=lambda sg: sg.name)
        return [copy.deepcopy(sg) for sg in items]

    async def save(self, sg: SecurityGroup) -> None:
        async with self._lock:
            self._items[sg.id] = copy.deepcopy(sg)

    async def delete(self, sg_id: SecurityGroupId) -> None:
        async with self._lock:
            self._items.pop(sg_id, None)


class InMemorySecurityGroupMemberRepository:
    def __init__(self) -> None:
        # key = (sg_id, member_type, member_value)
        self._items: dict[tuple[SecurityGroupId, str, str], SecurityGroupMember] = {}
        self._lock = anyio.Lock()

    async def list_for_group(self, sg_id: SecurityGroupId) -> list[SecurityGroupMember]:
        async with self._lock:
            items = [copy.deepcopy(m) for k, m in self._items.items() if k[0] == sg_id]
        items.sort(key=lambda m: (m.member_type, m.member_value))
        return items

    async def add(self, member: SecurityGroupMember) -> None:
        async with self._lock:
            self._items[(member.sg_id, member.member_type, member.member_value)] = (
                copy.deepcopy(member)
            )

    async def remove(
        self,
        sg_id: SecurityGroupId,
        member_type: str,
        member_value: str,
    ) -> None:
        async with self._lock:
            self._items.pop((sg_id, member_type, member_value), None)

    async def delete_for_group(self, sg_id: SecurityGroupId) -> None:
        async with self._lock:
            to_delete = [k for k in self._items if k[0] == sg_id]
            for k in to_delete:
                del self._items[k]


class InMemoryAddressPoolRepository:
    def __init__(self) -> None:
        self._items: dict[AddressPoolId, AddressPool] = {}
        self._lock = anyio.Lock()

    async def get(self, pool_id: AddressPoolId) -> AddressPool | None:
        async with self._lock:
            item = self._items.get(pool_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[AddressPool]:
        async with self._lock:
            items = list(self._items.values())
        if project_id is not None:
            items = [p for p in items if p.project_id == project_id]
        items.sort(key=lambda p: p.name)
        return [copy.deepcopy(p) for p in items]

    async def save(self, pool: AddressPool) -> None:
        async with self._lock:
            self._items[pool.id] = copy.deepcopy(pool)

    async def delete(self, pool_id: AddressPoolId) -> None:
        async with self._lock:
            self._items.pop(pool_id, None)


class InMemoryServiceObjectRepository:
    def __init__(self) -> None:
        self._items: dict[ServiceObjectId, ServiceObject] = {}
        self._lock = anyio.Lock()

    async def get(self, obj_id: ServiceObjectId) -> ServiceObject | None:
        async with self._lock:
            item = self._items.get(obj_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[ServiceObject]:
        async with self._lock:
            items = list(self._items.values())
        if project_id is not None:
            items = [o for o in items if o.project_id == project_id]
        items.sort(key=lambda o: o.name)
        return [copy.deepcopy(o) for o in items]

    async def save(self, obj: ServiceObject) -> None:
        async with self._lock:
            self._items[obj.id] = copy.deepcopy(obj)

    async def delete(self, obj_id: ServiceObjectId) -> None:
        async with self._lock:
            self._items.pop(obj_id, None)


class InMemoryQosPolicyRepository:
    def __init__(self) -> None:
        self._items: dict[QosPolicyId, QosPolicy] = {}
        self._lock = anyio.Lock()

    async def get(self, policy_id: QosPolicyId) -> QosPolicy | None:
        async with self._lock:
            item = self._items.get(policy_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[QosPolicy]:
        async with self._lock:
            items = list(self._items.values())
        if project_id is not None:
            items = [p for p in items if p.project_id == project_id]
        items.sort(key=lambda p: p.name)
        return [copy.deepcopy(p) for p in items]

    async def save(self, policy: QosPolicy) -> None:
        async with self._lock:
            self._items[policy.id] = copy.deepcopy(policy)

    async def delete(self, policy_id: QosPolicyId) -> None:
        async with self._lock:
            self._items.pop(policy_id, None)


class InMemorySecurityPolicyRepository:
    """Политики безопасности (N2-01)."""

    def __init__(self) -> None:
        self._items: dict[SecurityPolicyId, SecurityPolicy] = {}
        self._lock = anyio.Lock()

    async def get(self, policy_id: SecurityPolicyId) -> SecurityPolicy | None:
        async with self._lock:
            item = self._items.get(policy_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[SecurityPolicy]:
        async with self._lock:
            items = list(self._items.values())
        if project_id is not None:
            items = [p for p in items if p.project_id == project_id]
        items.sort(key=lambda p: p.name)
        return [copy.deepcopy(p) for p in items]

    async def save(self, policy: SecurityPolicy) -> None:
        async with self._lock:
            self._items[policy.id] = copy.deepcopy(policy)

    async def delete(self, policy_id: SecurityPolicyId) -> None:
        async with self._lock:
            self._items.pop(policy_id, None)


class InMemoryTrunkPortRepository:
    """Транковые порты 802.1q (N2-05)."""

    def __init__(self) -> None:
        self._items: dict[TrunkPortId, TrunkPort] = {}
        self._lock = anyio.Lock()

    async def get(self, port_id: TrunkPortId) -> TrunkPort | None:
        async with self._lock:
            item = self._items.get(port_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(
        self,
        *,
        node_id: NodeId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[TrunkPort]:
        async with self._lock:
            items = list(self._items.values())
        if node_id is not None:
            items = [p for p in items if p.node_id == node_id]
        if project_id is not None:
            items = [p for p in items if p.project_id == project_id]
        items.sort(key=lambda p: p.name)
        return [copy.deepcopy(p) for p in items]

    async def save(self, port: TrunkPort) -> None:
        async with self._lock:
            self._items[port.id] = copy.deepcopy(port)

    async def delete(self, port_id: TrunkPortId) -> None:
        async with self._lock:
            self._items.pop(port_id, None)


# ---------------------------------------------------------------------------
# N3 — Router, FloatingIP, BgpPeer
# ---------------------------------------------------------------------------


class InMemoryRouterRepository:
    """In-memory репозиторий для Router (N3-01)."""

    def __init__(self) -> None:
        self._items: dict[RouterId, Router] = {}
        self._lock = anyio.Lock()

    async def get(self, router_id: RouterId) -> Router | None:
        async with self._lock:
            item = self._items.get(router_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[Router]:
        async with self._lock:
            items = list(self._items.values())
        if project_id is not None:
            items = [r for r in items if r.project_id == project_id]
        items.sort(key=lambda r: r.name)
        return [copy.deepcopy(r) for r in items]

    async def save(self, router: Router) -> None:
        async with self._lock:
            self._items[router.id] = copy.deepcopy(router)

    async def delete(self, router_id: RouterId) -> None:
        async with self._lock:
            self._items.pop(router_id, None)


class InMemoryFloatingIpRepository:
    """In-memory репозиторий для FloatingIP (N3-02)."""

    def __init__(self) -> None:
        self._items: dict[FloatingIpId, FloatingIP] = {}
        self._lock = anyio.Lock()

    async def get(self, fip_id: FloatingIpId) -> FloatingIP | None:
        async with self._lock:
            item = self._items.get(fip_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(
        self,
        *,
        project_id: ProjectId | None = None,
        router_id: RouterId | None = None,
    ) -> list[FloatingIP]:
        async with self._lock:
            items = list(self._items.values())
        if project_id is not None:
            items = [f for f in items if f.project_id == project_id]
        if router_id is not None:
            items = [f for f in items if f.router_id == router_id]
        items.sort(key=lambda f: f.floating_ip_address)
        return [copy.deepcopy(f) for f in items]

    async def save(self, fip: FloatingIP) -> None:
        async with self._lock:
            self._items[fip.id] = copy.deepcopy(fip)

    async def delete(self, fip_id: FloatingIpId) -> None:
        async with self._lock:
            self._items.pop(fip_id, None)


class InMemoryBgpPeerRepository:
    """In-memory репозиторий для BgpPeer (N3-05)."""

    def __init__(self) -> None:
        self._items: dict[BgpPeerId, BgpPeer] = {}
        self._lock = anyio.Lock()

    async def get(self, peer_id: BgpPeerId) -> BgpPeer | None:
        async with self._lock:
            item = self._items.get(peer_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(
        self,
        *,
        router_id: RouterId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[BgpPeer]:
        async with self._lock:
            items = list(self._items.values())
        if router_id is not None:
            items = [p for p in items if p.router_id == router_id]
        if project_id is not None:
            items = [p for p in items if p.project_id == project_id]
        items.sort(key=lambda p: p.peer_ip)
        return [copy.deepcopy(p) for p in items]

    async def save(self, peer: BgpPeer) -> None:
        async with self._lock:
            self._items[peer.id] = copy.deepcopy(peer)

    async def delete(self, peer_id: BgpPeerId) -> None:
        async with self._lock:
            self._items.pop(peer_id, None)


# ---------------------------------------------------------------------------
# N4 — Governance & Scale in-memory repositories
# ---------------------------------------------------------------------------


class InMemoryProjectQuotaRepository:
    """In-memory репозиторий для ProjectQuota (N4-01)."""

    def __init__(self) -> None:
        self._items: dict[ProjectQuotaId, ProjectQuota] = {}
        self._lock = anyio.Lock()

    async def get_by_project(self, project_id: ProjectId) -> ProjectQuota | None:
        async with self._lock:
            for item in self._items.values():
                if item.project_id == project_id:
                    return copy.deepcopy(item)
            return None

    async def save(self, quota: ProjectQuota) -> None:
        async with self._lock:
            self._items[quota.id] = copy.deepcopy(quota)

    async def delete(self, quota_id: ProjectQuotaId) -> None:
        async with self._lock:
            self._items.pop(quota_id, None)


class InMemoryResourceSnapshotRepository:
    """In-memory репозиторий для ResourceSnapshot (N4-03)."""

    def __init__(self) -> None:
        self._items: dict[ResourceSnapshotId, ResourceSnapshot] = {}
        self._lock = anyio.Lock()

    async def get(self, snap_id: ResourceSnapshotId) -> ResourceSnapshot | None:
        async with self._lock:
            item = self._items.get(snap_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(
        self, *, project_id: ProjectId | None = None
    ) -> list[ResourceSnapshot]:
        async with self._lock:
            items = list(self._items.values())
        if project_id is not None:
            items = [s for s in items if s.project_id == project_id]
        items.sort(key=lambda s: s.version)
        return [copy.deepcopy(s) for s in items]

    async def save(self, snap: ResourceSnapshot) -> None:
        async with self._lock:
            self._items[snap.id] = copy.deepcopy(snap)

    async def delete(self, snap_id: ResourceSnapshotId) -> None:
        async with self._lock:
            self._items.pop(snap_id, None)


class InMemoryRetentionPolicyRepository:
    """In-memory репозиторий для RetentionPolicy (N4-05)."""

    def __init__(self) -> None:
        self._items: dict[RetentionPolicyId, RetentionPolicy] = {}
        self._lock = anyio.Lock()

    async def get(self, policy_id: RetentionPolicyId) -> RetentionPolicy | None:
        async with self._lock:
            item = self._items.get(policy_id)
            return copy.deepcopy(item) if item is not None else None

    async def get_by_scope(
        self,
        *,
        scope: RetentionScope,
        project_id: ProjectId | None = None,
    ) -> RetentionPolicy | None:
        async with self._lock:
            for item in self._items.values():
                if item.scope == scope and item.project_id == project_id:
                    return copy.deepcopy(item)
            return None

    async def list(self, *, project_id: ProjectId | None = None) -> list[RetentionPolicy]:
        async with self._lock:
            items = list(self._items.values())
        if project_id is not None:
            items = [p for p in items if p.project_id == project_id]
        items.sort(key=lambda p: p.scope.value)
        return [copy.deepcopy(p) for p in items]

    async def save(self, policy: RetentionPolicy) -> None:
        async with self._lock:
            self._items[policy.id] = copy.deepcopy(policy)

    async def delete(self, policy_id: RetentionPolicyId) -> None:
        async with self._lock:
            self._items.pop(policy_id, None)


class InMemoryGatewayBondRepository:
    """In-memory репозиторий для GatewayBond (N4-04)."""

    def __init__(self) -> None:
        self._items: dict[GatewayBondId, GatewayBond] = {}
        self._lock = anyio.Lock()

    async def get(self, bond_id: GatewayBondId) -> GatewayBond | None:
        async with self._lock:
            item = self._items.get(bond_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(
        self,
        *,
        node_id: NodeId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[GatewayBond]:
        async with self._lock:
            items = list(self._items.values())
        if node_id is not None:
            items = [b for b in items if b.node_id == node_id]
        if project_id is not None:
            items = [b for b in items if b.project_id == project_id]
        items.sort(key=lambda b: b.name)
        return [copy.deepcopy(b) for b in items]

    async def save(self, bond: GatewayBond) -> None:
        async with self._lock:
            self._items[bond.id] = copy.deepcopy(bond)

    async def delete(self, bond_id: GatewayBondId) -> None:
        async with self._lock:
            self._items.pop(bond_id, None)


class InMemoryLoadBalancerRepository:
    """In-memory репозиторий для LoadBalancer (N4-06)."""

    def __init__(self) -> None:
        self._items: dict[LoadBalancerId, LoadBalancer] = {}
        self._lock = anyio.Lock()

    async def get(self, lb_id: LoadBalancerId) -> LoadBalancer | None:
        async with self._lock:
            item = self._items.get(lb_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[LoadBalancer]:
        async with self._lock:
            items = list(self._items.values())
        if project_id is not None:
            items = [lb for lb in items if lb.project_id == project_id]
        items.sort(key=lambda lb: lb.name)
        return [copy.deepcopy(lb) for lb in items]

    async def save(self, lb: LoadBalancer) -> None:
        async with self._lock:
            self._items[lb.id] = copy.deepcopy(lb)

    async def delete(self, lb_id: LoadBalancerId) -> None:
        async with self._lock:
            self._items.pop(lb_id, None)


class InMemoryLbListenerRepository:
    """In-memory репозиторий для LbListener (N4-06)."""

    def __init__(self) -> None:
        self._items: dict[LbListenerId, LbListener] = {}
        self._lock = anyio.Lock()

    async def get(self, listener_id: LbListenerId) -> LbListener | None:
        async with self._lock:
            item = self._items.get(listener_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self, *, lb_id: LoadBalancerId | None = None) -> list[LbListener]:
        async with self._lock:
            items = list(self._items.values())
        if lb_id is not None:
            items = [l for l in items if l.lb_id == lb_id]
        items.sort(key=lambda l: l.protocol_port)
        return [copy.deepcopy(l) for l in items]

    async def save(self, listener: LbListener) -> None:
        async with self._lock:
            self._items[listener.id] = copy.deepcopy(listener)

    async def delete(self, listener_id: LbListenerId) -> None:
        async with self._lock:
            self._items.pop(listener_id, None)


class InMemoryLbPoolRepository:
    """In-memory репозиторий для LbPool (N4-06)."""

    def __init__(self) -> None:
        self._items: dict[LbPoolId, LbPool] = {}
        self._lock = anyio.Lock()

    async def get(self, pool_id: LbPoolId) -> LbPool | None:
        async with self._lock:
            item = self._items.get(pool_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self, *, lb_id: LoadBalancerId | None = None) -> list[LbPool]:
        async with self._lock:
            items = list(self._items.values())
        if lb_id is not None:
            items = [p for p in items if p.lb_id == lb_id]
        items.sort(key=lambda p: p.name)
        return [copy.deepcopy(p) for p in items]

    async def save(self, pool: LbPool) -> None:
        async with self._lock:
            self._items[pool.id] = copy.deepcopy(pool)

    async def delete(self, pool_id: LbPoolId) -> None:
        async with self._lock:
            self._items.pop(pool_id, None)


class InMemoryLbMemberRepository:
    """In-memory репозиторий для LbMember (N4-06)."""

    def __init__(self) -> None:
        self._items: dict[LbMemberId, LbMember] = {}
        self._lock = anyio.Lock()

    async def get(self, member_id: LbMemberId) -> LbMember | None:
        async with self._lock:
            item = self._items.get(member_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(self, *, pool_id: LbPoolId | None = None) -> list[LbMember]:
        async with self._lock:
            items = list(self._items.values())
        if pool_id is not None:
            items = [m for m in items if m.pool_id == pool_id]
        items.sort(key=lambda m: m.address)
        return [copy.deepcopy(m) for m in items]

    async def save(self, member: LbMember) -> None:
        async with self._lock:
            self._items[member.id] = copy.deepcopy(member)

    async def delete(self, member_id: LbMemberId) -> None:
        async with self._lock:
            self._items.pop(member_id, None)


class InMemoryHealthMonitorRepository:
    """In-memory репозиторий для HealthMonitor (N4-07)."""

    def __init__(self) -> None:
        self._items: dict[HealthMonitorId, HealthMonitor] = {}
        self._lock = anyio.Lock()

    async def get(self, monitor_id: HealthMonitorId) -> HealthMonitor | None:
        async with self._lock:
            item = self._items.get(monitor_id)
            return copy.deepcopy(item) if item is not None else None

    async def get_by_pool(self, pool_id: LbPoolId) -> HealthMonitor | None:
        async with self._lock:
            for item in self._items.values():
                if item.pool_id == pool_id:
                    return copy.deepcopy(item)
            return None

    async def save(self, monitor: HealthMonitor) -> None:
        async with self._lock:
            self._items[monitor.id] = copy.deepcopy(monitor)

    async def delete(self, monitor_id: HealthMonitorId) -> None:
        async with self._lock:
            self._items.pop(monitor_id, None)
