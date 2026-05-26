"""SQLAlchemy-backed repositories.

Each repository owns its session lifecycle: a method opens a session, runs in
a single transaction and commits before returning. We deliberately avoid a
unit-of-work that spans multiple repositories at this stage — the
milestone-1 use cases each touch a single aggregate.

Idempotency: ``save()`` upserts the aggregate. For aggregates with children
(``Network.subnet``, ``Operation.events``) we delete-and-rewrite the child
collection inside the same transaction; events are append-only at the domain
level, so replacing the rows produces an equivalent state without needing
event-by-event reconciliation.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdn_controller.adapters.sql import mappers, models
from sdn_controller.adapters.sql.models import (
    AddressPoolRow,
    AuditEventRow,
    BgpPeerRow,
    EnrollmentTokenRow,
    FloatingIpRow,
    IpAllocationRow,
    LogicalPortRow,
    NetworkRow,
    NodeRow,
    NodeSnapshotRow,
    ObservedStateRow,
    OperationEventRow,
    OperationRow,
    OutboxEventRow,
    ProjectMemberRow,
    ProjectRow,
    QosPolicyRow,
    RouterRow,
    SecurityGroupMemberRow,
    SecurityGroupRow,
    SecurityPolicyRow,
    ServiceAccountRow,
    ServiceObjectRow,
    ServiceTokenRow,
    SubnetRow,
    TrunkPortRow,
    WebhookSubscriptionRow,
)
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
    EnrollmentTokenId,
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
    SecurityGroupId,
    SecurityPolicyId,
    ServiceAccountId,
    ServiceObjectId,
    ServiceTokenId,
    SubnetId,
    BgpPeerId,
    FloatingIpId,
    RouterId,
    TrunkPortId,
    WebhookSubscriptionId,
)
from sdn_controller.core.value_objects.security import Role
from sdn_controller.core.value_objects.ipam import OwnerRef


class SqlNodeRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, node_id: NodeId) -> Node | None:
        async with self._session_factory() as session:
            row = await session.get(NodeRow, node_id)
            return mappers.node_from_row(row) if row is not None else None

    async def get_by_name(self, name: str) -> Node | None:
        async with self._session_factory() as session:
            row = (await session.scalars(select(NodeRow).where(NodeRow.name == name))).one_or_none()
            return mappers.node_from_row(row) if row is not None else None

    async def list(self) -> list[Node]:
        async with self._session_factory() as session:
            rows = (await session.scalars(select(NodeRow).order_by(NodeRow.name))).all()
            return [mappers.node_from_row(r) for r in rows]

    async def save(self, node: Node) -> None:
        async with self._session_factory() as session:
            existing = await session.get(NodeRow, node.id)
            if existing is None:
                session.add(mappers.node_to_row(node))
            else:
                _update_node_row(existing, node)
            await session.commit()

    async def delete(self, node_id: NodeId) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(NodeRow).where(NodeRow.id == node_id))
            await session.commit()


class SqlNetworkRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, network_id: NetworkId) -> Network | None:
        async with self._session_factory() as session:
            row = await session.get(NetworkRow, network_id)
            return mappers.network_from_row(row) if row is not None else None

    async def get_by_name(self, name: str) -> Network | None:
        async with self._session_factory() as session:
            row = (
                await session.scalars(select(NetworkRow).where(NetworkRow.name == name))
            ).one_or_none()
            return mappers.network_from_row(row) if row is not None else None

    async def get_by_subnet_id(self, subnet_id: SubnetId) -> Network | None:
        async with self._session_factory() as session:
            # ``SubnetRow.network_id`` is unique (1:1) so a single join is enough.
            row = (
                await session.scalars(
                    select(NetworkRow).join(SubnetRow).where(SubnetRow.id == subnet_id)
                )
            ).one_or_none()
            return mappers.network_from_row(row) if row is not None else None

    async def list(self) -> list[Network]:
        async with self._session_factory() as session:
            rows = (await session.scalars(select(NetworkRow).order_by(NetworkRow.created_at))).all()
            return [mappers.network_from_row(r) for r in rows]

    async def save(self, network: Network) -> None:
        async with self._session_factory() as session:
            existing = await session.get(NetworkRow, network.id)
            if existing is None:
                session.add(mappers.network_to_row(network))
            else:
                _update_network_row(existing, network)
            await session.commit()

    async def delete(self, network_id: NetworkId) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(NetworkRow).where(NetworkRow.id == network_id))
            await session.commit()


class SqlOperationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, operation_id: OperationId) -> Operation | None:
        async with self._session_factory() as session:
            row = await session.get(OperationRow, operation_id)
            return mappers.operation_from_row(row) if row is not None else None

    async def list(self, *, limit: int = 100) -> list[Operation]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(OperationRow).order_by(OperationRow.created_at.desc()).limit(limit)
                )
            ).all()
            return [mappers.operation_from_row(r) for r in rows]

    async def save(self, operation: Operation) -> None:
        async with self._session_factory() as session:
            existing = await session.get(OperationRow, operation.id)
            if existing is None:
                session.add(mappers.operation_to_row(operation))
            else:
                _update_operation_row(existing, operation)
                # Append-only at the domain level; rewrite child rows inside
                # the same tx to keep the table in sync with the aggregate.
                await session.execute(
                    delete(OperationEventRow).where(OperationEventRow.operation_id == operation.id)
                )
                for evt in operation.events:
                    session.add(mappers.operation_event_to_row(operation.id, evt))
            await session.commit()

    async def update_status(
        self,
        operation_id: OperationId,
        status: OperationStatus,
        event: OperationEvent,
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(OperationRow, operation_id)
            if row is None:
                raise NotFoundError(f"operation {operation_id} not found")
            row.status = status.value
            row.updated_at = event.at
            session.add(mappers.operation_event_to_row(operation_id, event))
            await session.commit()

    async def delete_terminal_before(self, cutoff: datetime) -> int:
        terminal_values = tuple(
            s.value
            for s in (
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED,
                OperationStatus.ROLLED_BACK,
            )
        )
        async with self._session_factory() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    delete(OperationRow).where(
                        OperationRow.status.in_(terminal_values),
                        OperationRow.updated_at < cutoff,
                    )
                ),
            )
            await session.commit()
            return result.rowcount or 0


class SqlEnrollmentTokenRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, token_id: EnrollmentTokenId) -> EnrollmentToken | None:
        async with self._session_factory() as session:
            row = await session.get(EnrollmentTokenRow, token_id)
            return mappers.enrollment_token_from_row(row) if row is not None else None

    async def get_by_hash(self, token_hash: str) -> EnrollmentToken | None:
        async with self._session_factory() as session:
            row = (
                await session.scalars(
                    select(EnrollmentTokenRow).where(EnrollmentTokenRow.token_hash == token_hash)
                )
            ).one_or_none()
            return mappers.enrollment_token_from_row(row) if row is not None else None

    async def list_for_node(self, node_id: NodeId) -> list[EnrollmentToken]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(EnrollmentTokenRow)
                    .where(EnrollmentTokenRow.node_id == node_id)
                    .order_by(EnrollmentTokenRow.issued_at.desc())
                )
            ).all()
            return [mappers.enrollment_token_from_row(r) for r in rows]

    async def save(self, token: EnrollmentToken) -> None:
        async with self._session_factory() as session:
            existing = await session.get(EnrollmentTokenRow, token.id)
            if existing is None:
                session.add(mappers.enrollment_token_to_row(token))
            else:
                _update_enrollment_token_row(existing, token)
            await session.commit()

    async def delete_for_node(self, node_id: NodeId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(EnrollmentTokenRow).where(EnrollmentTokenRow.node_id == node_id)
            )
            await session.commit()


class SqlObservedStateRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, node_id: NodeId) -> ObservedState | None:
        async with self._session_factory() as session:
            row = await session.get(ObservedStateRow, node_id)
            return mappers.observed_state_from_row(row) if row is not None else None

    async def save(self, state: ObservedState) -> None:
        async with self._session_factory() as session:
            existing = await session.get(ObservedStateRow, state.node_id)
            if existing is None:
                session.add(mappers.observed_state_to_row(state))
            else:
                _update_observed_state_row(existing, state)
            await session.commit()

    async def delete(self, node_id: NodeId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(ObservedStateRow).where(ObservedStateRow.node_id == node_id)
            )
            await session.commit()


# ---------------------------------------------------------------------------
# Private helpers — in-place row updates
# ---------------------------------------------------------------------------


def _update_node_row(row: models.NodeRow, node: Node) -> None:
    row.name = node.name
    row.mgmt_ip = node.mgmt_ip
    row.status = node.status.value
    row.roles = list(node.roles)
    row.labels = dict(node.labels)
    row.agent_version = node.agent_version
    row.last_seen_at = node.last_seen_at
    row.capabilities = mappers.capabilities_to_json(node.capabilities)
    row.tls_thumbprint = node.tls_thumbprint
    row.project_id = node.project_id
    row.maintenance = node.maintenance
    row.maintenance_at = node.maintenance_at
    row.created_at = node.created_at
    row.updated_at = node.updated_at


def _update_network_row(row: models.NetworkRow, network: Network) -> None:
    row.name = network.name
    row.type = network.type.value
    row.mtu = network.mtu
    row.vlan_id = network.vlan_id
    row.vni = network.vni
    row.labels = dict(network.labels)
    row.intent_version = network.intent_version
    row.node_ids = list(network.node_ids)
    row.spec_hash = network.spec_hash
    row.project_id = network.project_id
    row.created_at = network.created_at
    row.updated_at = network.updated_at
    if network.subnet is None:
        row.subnet = None
    else:
        row.subnet = mappers.subnet_to_row(network.subnet, network_id=network.id)


def _update_operation_row(row: models.OperationRow, op: Operation) -> None:
    row.kind = op.kind.value
    row.status = op.status.value
    row.resource_type = op.resource.type
    row.resource_id = op.resource.id
    row.created_at = op.created_at
    row.updated_at = op.updated_at
    row.created_by = op.created_by
    if op.error is None:
        row.error_code = None
        row.error_message = None
        row.error_details = None
    else:
        row.error_code = op.error.code
        row.error_message = op.error.message
        row.error_details = dict(op.error.details)


def _update_enrollment_token_row(row: models.EnrollmentTokenRow, token: EnrollmentToken) -> None:
    row.node_id = token.node_id
    row.token_hash = token.token_hash
    row.issued_at = token.issued_at
    row.expires_at = token.expires_at
    row.used_at = token.used_at
    row.issued_by = token.issued_by


def _update_observed_state_row(row: models.ObservedStateRow, state: ObservedState) -> None:
    fresh = mappers.observed_state_to_row(state)
    row.observed_at = fresh.observed_at
    row.state_hash = fresh.state_hash
    row.payload = fresh.payload


def _update_ip_allocation_row(row: models.IpAllocationRow, allocation: IpAllocation) -> None:
    row.subnet_id = allocation.subnet_id
    row.ip_address = allocation.ip_address
    row.owner_type = allocation.owner.type
    row.owner_id = allocation.owner.id
    row.kind = allocation.kind.value
    row.allocated_at = allocation.allocated_at
    row.label = allocation.label


class SqlIpAllocationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, allocation_id: IpAllocationId) -> IpAllocation | None:
        async with self._session_factory() as session:
            row = await session.get(IpAllocationRow, allocation_id)
            return mappers.ip_allocation_from_row(row) if row is not None else None

    async def get_by_address(self, subnet_id: SubnetId, address: str) -> IpAllocation | None:
        async with self._session_factory() as session:
            row = (
                await session.scalars(
                    select(IpAllocationRow).where(
                        IpAllocationRow.subnet_id == subnet_id,
                        IpAllocationRow.ip_address == address,
                    )
                )
            ).one_or_none()
            return mappers.ip_allocation_from_row(row) if row is not None else None

    async def list_for_subnet(self, subnet_id: SubnetId) -> list[IpAllocation]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(IpAllocationRow)
                    .where(IpAllocationRow.subnet_id == subnet_id)
                    .order_by(IpAllocationRow.ip_address)
                )
            ).all()
            return [mappers.ip_allocation_from_row(r) for r in rows]

    async def list_for_owner(self, owner: OwnerRef) -> list[IpAllocation]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(IpAllocationRow)
                    .where(
                        IpAllocationRow.owner_type == owner.type,
                        IpAllocationRow.owner_id == owner.id,
                    )
                    .order_by(IpAllocationRow.allocated_at)
                )
            ).all()
            return [mappers.ip_allocation_from_row(r) for r in rows]

    async def save(self, allocation: IpAllocation) -> None:
        async with self._session_factory() as session:
            existing = await session.get(IpAllocationRow, allocation.id)
            if existing is None:
                session.add(mappers.ip_allocation_to_row(allocation))
            else:
                _update_ip_allocation_row(existing, allocation)
            await session.commit()

    async def delete(self, allocation_id: IpAllocationId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(IpAllocationRow).where(IpAllocationRow.id == allocation_id)
            )
            await session.commit()


class SqlServiceAccountRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, account_id: ServiceAccountId) -> ServiceAccount | None:
        async with self._session_factory() as session:
            row = await session.get(ServiceAccountRow, account_id)
            return mappers.service_account_from_row(row) if row is not None else None

    async def get_by_name(self, name: str) -> ServiceAccount | None:
        async with self._session_factory() as session:
            row = (
                await session.scalars(
                    select(ServiceAccountRow).where(ServiceAccountRow.name == name)
                )
            ).one_or_none()
            return mappers.service_account_from_row(row) if row is not None else None

    async def list(self) -> list[ServiceAccount]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(select(ServiceAccountRow).order_by(ServiceAccountRow.name))
            ).all()
            return [mappers.service_account_from_row(r) for r in rows]

    async def save(self, account: ServiceAccount) -> None:
        async with self._session_factory() as session:
            existing = await session.get(ServiceAccountRow, account.id)
            if existing is None:
                session.add(mappers.service_account_to_row(account))
            else:
                _update_service_account_row(existing, account)
            await session.commit()


class SqlServiceTokenRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, token_id: ServiceTokenId) -> ServiceToken | None:
        async with self._session_factory() as session:
            row = await session.get(ServiceTokenRow, token_id)
            return mappers.service_token_from_row(row) if row is not None else None

    async def get_by_hash(self, token_hash: str) -> ServiceToken | None:
        async with self._session_factory() as session:
            row = (
                await session.scalars(
                    select(ServiceTokenRow).where(ServiceTokenRow.token_hash == token_hash)
                )
            ).one_or_none()
            return mappers.service_token_from_row(row) if row is not None else None

    async def list_for_account(self, account_id: ServiceAccountId) -> list[ServiceToken]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ServiceTokenRow)
                    .where(ServiceTokenRow.service_account_id == account_id)
                    .order_by(ServiceTokenRow.issued_at)
                )
            ).all()
            return [mappers.service_token_from_row(r) for r in rows]

    async def save(self, token: ServiceToken) -> None:
        async with self._session_factory() as session:
            existing = await session.get(ServiceTokenRow, token.id)
            if existing is None:
                session.add(mappers.service_token_to_row(token))
            else:
                _update_service_token_row(existing, token)
            await session.commit()


def _update_service_account_row(row: ServiceAccountRow, account: ServiceAccount) -> None:
    row.name = account.name
    row.role = account.role.value
    row.description = account.description
    row.labels = dict(account.labels)
    row.created_at = account.created_at
    row.updated_at = account.updated_at
    row.created_by = account.created_by
    row.disabled_at = account.disabled_at


def _update_service_token_row(row: ServiceTokenRow, token: ServiceToken) -> None:
    row.service_account_id = token.service_account_id
    row.token_hash = token.token_hash
    row.issued_at = token.issued_at
    row.expires_at = token.expires_at
    row.last_used_at = token.last_used_at
    row.revoked_at = token.revoked_at
    row.issued_by = token.issued_by
    row.label = token.label


class SqlAuditEventRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, event: AuditEvent) -> None:
        async with self._session_factory() as session:
            session.add(mappers.audit_event_to_row(event))
            await session.commit()

    async def get(self, event_id: AuditEventId) -> AuditEvent | None:
        async with self._session_factory() as session:
            row = await session.get(AuditEventRow, event_id)
            return mappers.audit_event_from_row(row) if row is not None else None

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
        async with self._session_factory() as session:
            stmt = select(AuditEventRow).order_by(AuditEventRow.at.desc()).limit(limit)
            if actor is not None:
                stmt = stmt.where(AuditEventRow.actor == actor)
            if action is not None:
                stmt = stmt.where(AuditEventRow.action == action)
            if resource_type is not None:
                stmt = stmt.where(AuditEventRow.resource_type == resource_type)
            if resource_id is not None:
                stmt = stmt.where(AuditEventRow.resource_id == resource_id)
            if since is not None:
                stmt = stmt.where(AuditEventRow.at >= since)
            rows = (await session.scalars(stmt)).all()
            return [mappers.audit_event_from_row(r) for r in rows]

    async def list_before(self, cutoff: datetime, *, limit: int = 1000) -> Sequence[AuditEvent]:
        async with self._session_factory() as session:
            stmt = (
                select(AuditEventRow)
                .where(AuditEventRow.at < cutoff)
                .order_by(AuditEventRow.at.asc())
                .limit(limit)
            )
            rows = (await session.scalars(stmt)).all()
            return [mappers.audit_event_from_row(r) for r in rows]

    async def delete_before(self, cutoff: datetime) -> int:
        async with self._session_factory() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(delete(AuditEventRow).where(AuditEventRow.at < cutoff)),
            )
            await session.commit()
            return result.rowcount or 0

    async def delete_many(self, event_ids: Sequence[AuditEventId]) -> int:
        if not event_ids:
            return 0
        async with self._session_factory() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(delete(AuditEventRow).where(AuditEventRow.id.in_(event_ids))),
            )
            await session.commit()
            return result.rowcount or 0


class SqlNodeSnapshotRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, snapshot: NodeSnapshot) -> None:
        async with self._session_factory() as session:
            existing = await session.get(NodeSnapshotRow, snapshot.id)
            if existing is None:
                session.add(mappers.node_snapshot_to_row(snapshot))
            else:
                existing.node_id = snapshot.node_id
                existing.agent_snapshot_id = snapshot.agent_snapshot_id
                existing.state_hash = snapshot.state_hash
                existing.created_at = snapshot.created_at
                existing.label = snapshot.label
            await session.commit()

    async def get(self, snapshot_id: NodeSnapshotId) -> NodeSnapshot | None:
        async with self._session_factory() as session:
            row = await session.get(NodeSnapshotRow, snapshot_id)
            return mappers.node_snapshot_from_row(row) if row is not None else None

    async def list_for_node(self, node_id: NodeId) -> list[NodeSnapshot]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(NodeSnapshotRow)
                    .where(NodeSnapshotRow.node_id == node_id)
                    .order_by(NodeSnapshotRow.created_at.desc())
                )
            ).all()
            return [mappers.node_snapshot_from_row(r) for r in rows]

    async def list(self, *, limit: int = 200) -> list[NodeSnapshot]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(NodeSnapshotRow).order_by(NodeSnapshotRow.created_at.desc()).limit(limit)
                )
            ).all()
            return [mappers.node_snapshot_from_row(r) for r in rows]

    async def delete(self, snapshot_id: NodeSnapshotId) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(NodeSnapshotRow).where(NodeSnapshotRow.id == snapshot_id))
            await session.commit()


class SqlOutboxRepository:
    """``outbox_events`` — monotonic event stream (SDN-055).

    ``append`` сохраняет строку с pending ``event_id=None``; драйвер
    подставит autoincrement, ``session.refresh`` подтянет получившееся
    значение обратно в materialized-объект.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(self, event: OutboxEvent) -> OutboxEvent:
        async with self._session_factory() as session:
            row = mappers.outbox_event_to_row(event)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return mappers.outbox_event_from_row(row)

    async def get(self, event_id: OutboxEventId) -> OutboxEvent | None:
        async with self._session_factory() as session:
            row = await session.get(OutboxEventRow, event_id)
            return mappers.outbox_event_from_row(row) if row is not None else None

    async def list_since(self, *, since: int = 0, limit: int = 200) -> Sequence[OutboxEvent]:
        async with self._session_factory() as session:
            stmt = (
                select(OutboxEventRow)
                .where(OutboxEventRow.event_id > since)
                .order_by(OutboxEventRow.event_id.asc())
                .limit(limit)
            )
            rows = (await session.scalars(stmt)).all()
            return [mappers.outbox_event_from_row(r) for r in rows]

    async def list_undelivered(self, *, limit: int = 200) -> Sequence[OutboxEvent]:
        async with self._session_factory() as session:
            stmt = (
                select(OutboxEventRow)
                .where(OutboxEventRow.delivered_at.is_(None))
                .order_by(OutboxEventRow.event_id.asc())
                .limit(limit)
            )
            rows = (await session.scalars(stmt)).all()
            return [mappers.outbox_event_from_row(r) for r in rows]

    async def mark_delivered(self, event_ids: Sequence[OutboxEventId], *, at: datetime) -> None:
        if not event_ids:
            return
        async with self._session_factory() as session:
            await session.execute(
                update(OutboxEventRow)
                .where(
                    OutboxEventRow.id.in_(event_ids),
                    OutboxEventRow.delivered_at.is_(None),
                )
                .values(delivered_at=at)
            )
            await session.commit()

    async def head_event_id(self) -> int:
        async with self._session_factory() as session:
            value = await session.scalar(select(func.max(OutboxEventRow.event_id)))
            return int(value or 0)

    async def delete_delivered_before(self, cutoff: datetime) -> int:
        async with self._session_factory() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    delete(OutboxEventRow).where(
                        OutboxEventRow.delivered_at.is_not(None),
                        OutboxEventRow.delivered_at < cutoff,
                    )
                ),
            )
            await session.commit()
            return result.rowcount or 0


class SqlWebhookSubscriptionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, subscription: WebhookSubscription) -> None:
        async with self._session_factory() as session:
            await session.merge(mappers.webhook_subscription_to_row(subscription))
            await session.commit()

    async def get(self, sub_id: WebhookSubscriptionId) -> WebhookSubscription | None:
        async with self._session_factory() as session:
            row = await session.get(WebhookSubscriptionRow, sub_id)
            return mappers.webhook_subscription_from_row(row) if row is not None else None

    async def list(self) -> Sequence[WebhookSubscription]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(WebhookSubscriptionRow).order_by(WebhookSubscriptionRow.created_at.asc())
                )
            ).all()
            return [mappers.webhook_subscription_from_row(r) for r in rows]

    async def list_active(self) -> Sequence[WebhookSubscription]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(WebhookSubscriptionRow)
                    .where(WebhookSubscriptionRow.state == WebhookSubscriptionState.ACTIVE.value)
                    .order_by(WebhookSubscriptionRow.created_at.asc())
                )
            ).all()
            return [mappers.webhook_subscription_from_row(r) for r in rows]

    async def delete(self, sub_id: WebhookSubscriptionId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(WebhookSubscriptionRow).where(WebhookSubscriptionRow.id == sub_id)
            )
            await session.commit()


class SqlProjectRepository:
    """Projects (N0 — multitenancy)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, project_id: ProjectId) -> Project | None:
        async with self._session_factory() as session:
            row = await session.get(ProjectRow, project_id)
            return mappers.project_from_row(row) if row is not None else None

    async def get_by_slug(self, slug: str) -> Project | None:
        async with self._session_factory() as session:
            row = (
                await session.scalars(select(ProjectRow).where(ProjectRow.slug == slug))
            ).one_or_none()
            return mappers.project_from_row(row) if row is not None else None

    async def list(self) -> list[Project]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(select(ProjectRow).order_by(ProjectRow.created_at.asc()))
            ).all()
            return [mappers.project_from_row(r) for r in rows]

    async def save(self, project: Project) -> None:
        async with self._session_factory() as session:
            existing = await session.get(ProjectRow, project.id)
            if existing is None:
                session.add(mappers.project_to_row(project))
            else:
                existing.name = project.name
                existing.slug = project.slug
                existing.description = project.description
                existing.labels = dict(project.labels)
                existing.updated_at = project.updated_at
            await session.commit()

    async def delete(self, project_id: ProjectId) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(ProjectRow).where(ProjectRow.id == project_id))
            await session.commit()


class SqlProjectMemberRepository:
    """Project member bindings (N0)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(
        self, project_id: ProjectId, sa_id: ServiceAccountId
    ) -> ProjectMember | None:
        async with self._session_factory() as session:
            row = await session.get(ProjectMemberRow, (project_id, sa_id))
            return mappers.project_member_from_row(row) if row is not None else None

    async def list_for_project(self, project_id: ProjectId) -> list[ProjectMember]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ProjectMemberRow)
                    .where(ProjectMemberRow.project_id == project_id)
                    .order_by(ProjectMemberRow.created_at.asc())
                )
            ).all()
            return [mappers.project_member_from_row(r) for r in rows]

    async def list_for_account(self, sa_id: ServiceAccountId) -> list[ProjectMember]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(ProjectMemberRow)
                    .where(ProjectMemberRow.service_account_id == sa_id)
                    .order_by(ProjectMemberRow.created_at.asc())
                )
            ).all()
            return [mappers.project_member_from_row(r) for r in rows]

    async def save(self, member: ProjectMember) -> None:
        async with self._session_factory() as session:
            await session.merge(mappers.project_member_to_row(member))
            await session.commit()

    async def delete(self, project_id: ProjectId, sa_id: ServiceAccountId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(ProjectMemberRow).where(
                    ProjectMemberRow.project_id == project_id,
                    ProjectMemberRow.service_account_id == sa_id,
                )
            )
            await session.commit()

    async def has_role(
        self, project_id: ProjectId, sa_id: ServiceAccountId, role: Role
    ) -> bool:
        member = await self.get(project_id, sa_id)
        if member is None:
            return False
        # admin in project has all roles
        if member.role == Role.ADMIN:
            return True
        return member.role == role


# ---------------------------------------------------------------------------
# N1 SQL repositories
# ---------------------------------------------------------------------------


class SqlLogicalPortRepository:
    """Logical ports (N1-01)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, port_id: LogicalPortId) -> LogicalPort | None:
        async with self._session_factory() as session:
            row = await session.get(LogicalPortRow, port_id)
            return mappers.logical_port_from_row(row) if row is not None else None

    async def list(
        self,
        *,
        node_id: NodeId | None = None,
        network_id: NetworkId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[LogicalPort]:
        async with self._session_factory() as session:
            stmt = select(LogicalPortRow)
            if node_id is not None:
                stmt = stmt.where(LogicalPortRow.node_id == node_id)
            if network_id is not None:
                stmt = stmt.where(LogicalPortRow.network_id == network_id)
            if project_id is not None:
                stmt = stmt.where(LogicalPortRow.project_id == project_id)
            stmt = stmt.order_by(LogicalPortRow.created_at.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.logical_port_from_row(r) for r in rows]

    async def save(self, port: LogicalPort) -> None:
        async with self._session_factory() as session:
            existing = await session.get(LogicalPortRow, port.id)
            if existing is None:
                session.add(mappers.logical_port_to_row(port))
            else:
                existing.name = port.name
                existing.node_id = port.node_id
                existing.network_id = port.network_id
                existing.vif_id = port.vif_id
                existing.mac_address = port.mac_address
                existing.ip_address = port.ip_address
                existing.status = port.status.value
                existing.project_id = port.project_id
                existing.labels = dict(port.labels)
                existing.updated_at = port.updated_at
            await session.commit()

    async def delete(self, port_id: LogicalPortId) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(LogicalPortRow).where(LogicalPortRow.id == port_id))
            await session.commit()

    async def delete_for_node(self, node_id: NodeId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(LogicalPortRow).where(LogicalPortRow.node_id == node_id)
            )
            await session.commit()


class SqlSecurityGroupRepository:
    """Security groups (N1-02)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, sg_id: SecurityGroupId) -> SecurityGroup | None:
        async with self._session_factory() as session:
            row = await session.get(SecurityGroupRow, sg_id)
            return mappers.security_group_from_row(row) if row is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[SecurityGroup]:
        async with self._session_factory() as session:
            stmt = select(SecurityGroupRow)
            if project_id is not None:
                stmt = stmt.where(SecurityGroupRow.project_id == project_id)
            stmt = stmt.order_by(SecurityGroupRow.name.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.security_group_from_row(r) for r in rows]

    async def save(self, sg: SecurityGroup) -> None:
        async with self._session_factory() as session:
            existing = await session.get(SecurityGroupRow, sg.id)
            if existing is None:
                session.add(mappers.security_group_to_row(sg))
            else:
                existing.name = sg.name
                existing.description = sg.description
                existing.project_id = sg.project_id
                existing.labels = dict(sg.labels)
                existing.updated_at = sg.updated_at
            await session.commit()

    async def delete(self, sg_id: SecurityGroupId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(SecurityGroupRow).where(SecurityGroupRow.id == sg_id)
            )
            await session.commit()


class SqlSecurityGroupMemberRepository:
    """Security group members (N1-02)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_for_group(self, sg_id: SecurityGroupId) -> list[SecurityGroupMember]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(SecurityGroupMemberRow)
                    .where(SecurityGroupMemberRow.sg_id == sg_id)
                    .order_by(
                        SecurityGroupMemberRow.member_type.asc(),
                        SecurityGroupMemberRow.member_value.asc(),
                    )
                )
            ).all()
            return [mappers.sg_member_from_row(r) for r in rows]

    async def add(self, member: SecurityGroupMember) -> None:
        async with self._session_factory() as session:
            await session.merge(mappers.sg_member_to_row(member))
            await session.commit()

    async def remove(
        self,
        sg_id: SecurityGroupId,
        member_type: str,
        member_value: str,
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(SecurityGroupMemberRow).where(
                    SecurityGroupMemberRow.sg_id == sg_id,
                    SecurityGroupMemberRow.member_type == member_type,
                    SecurityGroupMemberRow.member_value == member_value,
                )
            )
            await session.commit()

    async def delete_for_group(self, sg_id: SecurityGroupId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(SecurityGroupMemberRow).where(SecurityGroupMemberRow.sg_id == sg_id)
            )
            await session.commit()


class SqlAddressPoolRepository:
    """Address pools (N1-03)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, pool_id: AddressPoolId) -> AddressPool | None:
        async with self._session_factory() as session:
            row = await session.get(AddressPoolRow, pool_id)
            return mappers.address_pool_from_row(row) if row is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[AddressPool]:
        async with self._session_factory() as session:
            stmt = select(AddressPoolRow)
            if project_id is not None:
                stmt = stmt.where(AddressPoolRow.project_id == project_id)
            stmt = stmt.order_by(AddressPoolRow.name.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.address_pool_from_row(r) for r in rows]

    async def save(self, pool: AddressPool) -> None:
        async with self._session_factory() as session:
            existing = await session.get(AddressPoolRow, pool.id)
            if existing is None:
                session.add(mappers.address_pool_to_row(pool))
            else:
                existing.name = pool.name
                existing.description = pool.description
                existing.project_id = pool.project_id
                existing.cidrs = list(pool.cidrs)
                existing.labels = dict(pool.labels)
                existing.updated_at = pool.updated_at
            await session.commit()

    async def delete(self, pool_id: AddressPoolId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(AddressPoolRow).where(AddressPoolRow.id == pool_id)
            )
            await session.commit()


class SqlServiceObjectRepository:
    """Service objects (N1-04)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, obj_id: ServiceObjectId) -> ServiceObject | None:
        async with self._session_factory() as session:
            row = await session.get(ServiceObjectRow, obj_id)
            return mappers.service_object_from_row(row) if row is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[ServiceObject]:
        async with self._session_factory() as session:
            stmt = select(ServiceObjectRow)
            if project_id is not None:
                stmt = stmt.where(ServiceObjectRow.project_id == project_id)
            stmt = stmt.order_by(ServiceObjectRow.name.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.service_object_from_row(r) for r in rows]

    async def save(self, obj: ServiceObject) -> None:
        async with self._session_factory() as session:
            existing = await session.get(ServiceObjectRow, obj.id)
            if existing is None:
                session.add(mappers.service_object_to_row(obj))
            else:
                existing.name = obj.name
                existing.description = obj.description
                existing.project_id = obj.project_id
                existing.protocol = obj.protocol
                existing.ports = list(obj.ports)
                existing.labels = dict(obj.labels)
                existing.updated_at = obj.updated_at
            await session.commit()

    async def delete(self, obj_id: ServiceObjectId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(ServiceObjectRow).where(ServiceObjectRow.id == obj_id)
            )
            await session.commit()


class SqlQosPolicyRepository:
    """QoS policies (N1-05)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, policy_id: QosPolicyId) -> QosPolicy | None:
        async with self._session_factory() as session:
            row = await session.get(QosPolicyRow, policy_id)
            return mappers.qos_policy_from_row(row) if row is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[QosPolicy]:
        async with self._session_factory() as session:
            stmt = select(QosPolicyRow)
            if project_id is not None:
                stmt = stmt.where(QosPolicyRow.project_id == project_id)
            stmt = stmt.order_by(QosPolicyRow.name.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.qos_policy_from_row(r) for r in rows]

    async def save(self, policy: QosPolicy) -> None:
        async with self._session_factory() as session:
            existing = await session.get(QosPolicyRow, policy.id)
            if existing is None:
                session.add(mappers.qos_policy_to_row(policy))
            else:
                existing.name = policy.name
                existing.description = policy.description
                existing.project_id = policy.project_id
                existing.ingress_kbps = policy.ingress_kbps
                existing.egress_kbps = policy.egress_kbps
                existing.burst_kb = policy.burst_kb
                existing.dscp = policy.dscp
                existing.labels = dict(policy.labels)
                existing.updated_at = policy.updated_at
            await session.commit()

    async def delete(self, policy_id: QosPolicyId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(QosPolicyRow).where(QosPolicyRow.id == policy_id)
            )
            await session.commit()


class SqlSecurityPolicyRepository:
    """Политики безопасности (N2-01, N2-03)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, policy_id: SecurityPolicyId) -> SecurityPolicy | None:
        from sqlalchemy.orm import selectinload
        async with self._session_factory() as session:
            stmt = (
                select(SecurityPolicyRow)
                .where(SecurityPolicyRow.id == policy_id)
                .options(selectinload(SecurityPolicyRow.rules))
            )
            row = (await session.scalars(stmt)).first()
            return mappers.security_policy_from_row(row) if row is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[SecurityPolicy]:
        from sqlalchemy.orm import selectinload
        async with self._session_factory() as session:
            stmt = select(SecurityPolicyRow).options(selectinload(SecurityPolicyRow.rules))
            if project_id is not None:
                stmt = stmt.where(SecurityPolicyRow.project_id == project_id)
            stmt = stmt.order_by(SecurityPolicyRow.name.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.security_policy_from_row(r) for r in rows]

    async def save(self, policy: SecurityPolicy) -> None:
        from sqlalchemy.orm import selectinload
        async with self._session_factory() as session:
            existing = (await session.scalars(
                select(SecurityPolicyRow)
                .where(SecurityPolicyRow.id == policy.id)
                .options(selectinload(SecurityPolicyRow.rules))
            )).first()
            if existing is None:
                session.add(mappers.security_policy_to_row(policy))
            else:
                existing.name = policy.name
                existing.description = policy.description
                existing.project_id = policy.project_id
                existing.labels = dict(policy.labels)
                existing.status = str(policy.status)
                existing.compiled_ruleset = policy.compiled_ruleset
                existing.compiled_at = policy.compiled_at
                existing.applied_at = policy.applied_at
                existing.updated_at = policy.updated_at
                # Заменяем правила целиком через delete + insert
                for rule_row in list(existing.rules):
                    await session.delete(rule_row)
                await session.flush()
                for rule in policy.rules:
                    session.add(mappers.security_policy_rule_to_row(rule, str(policy.id)))
            await session.commit()

    async def delete(self, policy_id: SecurityPolicyId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(SecurityPolicyRow).where(SecurityPolicyRow.id == policy_id)
            )
            await session.commit()


class SqlTrunkPortRepository:
    """Транковые порты 802.1q (N2-05)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, port_id: TrunkPortId) -> TrunkPort | None:
        async with self._session_factory() as session:
            row = await session.get(TrunkPortRow, port_id)
            return mappers.trunk_port_from_row(row) if row is not None else None

    async def list(
        self,
        *,
        node_id: NodeId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[TrunkPort]:
        async with self._session_factory() as session:
            stmt = select(TrunkPortRow)
            if node_id is not None:
                stmt = stmt.where(TrunkPortRow.node_id == node_id)
            if project_id is not None:
                stmt = stmt.where(TrunkPortRow.project_id == project_id)
            stmt = stmt.order_by(TrunkPortRow.name.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.trunk_port_from_row(r) for r in rows]

    async def save(self, port: TrunkPort) -> None:
        async with self._session_factory() as session:
            existing = await session.get(TrunkPortRow, port.id)
            if existing is None:
                session.add(mappers.trunk_port_to_row(port))
            else:
                existing.name = port.name
                existing.node_id = port.node_id
                existing.logical_port_id = str(port.logical_port_id) if port.logical_port_id else None
                existing.vlan_ids = list(port.vlan_ids)
                existing.native_vlan = port.native_vlan
                existing.project_id = port.project_id
                existing.labels = dict(port.labels)
                existing.updated_at = port.updated_at
            await session.commit()

    async def delete(self, port_id: TrunkPortId) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(TrunkPortRow).where(TrunkPortRow.id == port_id)
            )
            await session.commit()


# ---------------------------------------------------------------------------
# N3 — Router, FloatingIP, BgpPeer
# ---------------------------------------------------------------------------


class SqlRouterRepository:
    """SQL-репозиторий для Router (N3-01)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, router_id: RouterId) -> Router | None:
        async with self._session_factory() as session:
            row = await session.get(RouterRow, router_id)
            return mappers.router_from_row(row) if row is not None else None

    async def list(self, *, project_id: ProjectId | None = None) -> list[Router]:
        async with self._session_factory() as session:
            stmt = select(RouterRow)
            if project_id is not None:
                stmt = stmt.where(RouterRow.project_id == project_id)
            stmt = stmt.order_by(RouterRow.name.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.router_from_row(r) for r in rows]

    async def save(self, router: Router) -> None:
        async with self._session_factory() as session:
            existing = await session.get(RouterRow, router.id)
            if existing is None:
                session.add(mappers.router_to_row(router))
            else:
                existing.name = router.name
                existing.description = router.description
                existing.project_id = router.project_id
                existing.external_network_id = router.external_network_id
                existing.internal_network_ids = sorted(router.internal_network_ids)
                existing.static_routes = [
                    {"destination": r.destination, "nexthop": r.nexthop}
                    for r in router.static_routes
                ]
                existing.status = router.status.value
                existing.admin_state_up = router.admin_state_up
                existing.ha_mode = router.ha_mode.value
                existing.vrrp_priority = router.vrrp_priority
                existing.vrrp_vrid = router.vrrp_vrid
                from sdn_controller.adapters.sql.mappers import _ipv6_config_to_dict
                existing.ipv6_config = (
                    _ipv6_config_to_dict(router.ipv6_config) if router.ipv6_config else None
                )
                existing.applied_config = router.applied_config
                existing.applied_at = router.applied_at
                existing.labels = dict(router.labels)
                existing.updated_at = router.updated_at
            await session.commit()

    async def delete(self, router_id: RouterId) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(RouterRow).where(RouterRow.id == router_id))
            await session.commit()


class SqlFloatingIpRepository:
    """SQL-репозиторий для FloatingIP (N3-02)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, fip_id: FloatingIpId) -> FloatingIP | None:
        async with self._session_factory() as session:
            row = await session.get(FloatingIpRow, fip_id)
            return mappers.floating_ip_from_row(row) if row is not None else None

    async def list(
        self,
        *,
        project_id: ProjectId | None = None,
        router_id: RouterId | None = None,
    ) -> list[FloatingIP]:
        async with self._session_factory() as session:
            stmt = select(FloatingIpRow)
            if project_id is not None:
                stmt = stmt.where(FloatingIpRow.project_id == project_id)
            if router_id is not None:
                stmt = stmt.where(FloatingIpRow.router_id == router_id)
            stmt = stmt.order_by(FloatingIpRow.floating_ip_address.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.floating_ip_from_row(r) for r in rows]

    async def save(self, fip: FloatingIP) -> None:
        async with self._session_factory() as session:
            existing = await session.get(FloatingIpRow, fip.id)
            if existing is None:
                session.add(mappers.floating_ip_to_row(fip))
            else:
                existing.fixed_ip_address = fip.fixed_ip_address
                existing.logical_port_id = str(fip.logical_port_id) if fip.logical_port_id else None
                existing.router_id = str(fip.router_id) if fip.router_id else None
                existing.status = fip.status.value
                existing.labels = dict(fip.labels)
                existing.updated_at = fip.updated_at
            await session.commit()

    async def delete(self, fip_id: FloatingIpId) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(FloatingIpRow).where(FloatingIpRow.id == fip_id))
            await session.commit()


class SqlBgpPeerRepository:
    """SQL-репозиторий для BgpPeer (N3-05)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, peer_id: BgpPeerId) -> BgpPeer | None:
        async with self._session_factory() as session:
            row = await session.get(BgpPeerRow, peer_id)
            return mappers.bgp_peer_from_row(row) if row is not None else None

    async def list(
        self,
        *,
        router_id: RouterId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[BgpPeer]:
        async with self._session_factory() as session:
            stmt = select(BgpPeerRow)
            if router_id is not None:
                stmt = stmt.where(BgpPeerRow.router_id == router_id)
            if project_id is not None:
                stmt = stmt.where(BgpPeerRow.project_id == project_id)
            stmt = stmt.order_by(BgpPeerRow.peer_ip.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.bgp_peer_from_row(r) for r in rows]

    async def save(self, peer: BgpPeer) -> None:
        async with self._session_factory() as session:
            existing = await session.get(BgpPeerRow, peer.id)
            if existing is None:
                session.add(mappers.bgp_peer_to_row(peer))
            else:
                existing.password = peer.password
                existing.state = peer.state.value
                existing.labels = dict(peer.labels)
                existing.updated_at = peer.updated_at
            await session.commit()

    async def delete(self, peer_id: BgpPeerId) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(BgpPeerRow).where(BgpPeerRow.id == peer_id))
            await session.commit()


# ---------------------------------------------------------------------------
# N4 — Governance & Scale SQL repositories
# ---------------------------------------------------------------------------


class SqlProjectQuotaRepository:
    """SQL-репозиторий для ProjectQuota (N4-01)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_by_project(self, project_id: "ProjectId") -> "ProjectQuota | None":
        from sdn_controller.adapters.sql.models import ProjectQuotaRow
        from sdn_controller.core.entities import ProjectQuota as _PQ
        from sdn_controller.core.value_objects.ids import ProjectId as _PId
        async with self._session_factory() as session:
            stmt = select(ProjectQuotaRow).where(ProjectQuotaRow.project_id == project_id)
            row = (await session.scalars(stmt)).first()
            return mappers.project_quota_from_row(row) if row is not None else None

    async def save(self, quota: "ProjectQuota") -> None:
        from sdn_controller.adapters.sql.models import ProjectQuotaRow
        async with self._session_factory() as session:
            existing = await session.get(ProjectQuotaRow, quota.id)
            if existing is None:
                session.add(mappers.project_quota_to_row(quota))
            else:
                existing.limits = dict(quota.limits)
                existing.updated_at = quota.updated_at
            await session.commit()

    async def delete(self, quota_id: "ProjectQuotaId") -> None:
        from sdn_controller.adapters.sql.models import ProjectQuotaRow
        async with self._session_factory() as session:
            await session.execute(delete(ProjectQuotaRow).where(ProjectQuotaRow.id == quota_id))
            await session.commit()


class SqlResourceSnapshotRepository:
    """SQL-репозиторий для ResourceSnapshot (N4-03)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, snap_id: "ResourceSnapshotId") -> "ResourceSnapshot | None":
        from sdn_controller.adapters.sql.models import ResourceSnapshotRow
        async with self._session_factory() as session:
            row = await session.get(ResourceSnapshotRow, snap_id)
            return mappers.resource_snapshot_from_row(row) if row is not None else None

    async def list(self, *, project_id: "ProjectId | None" = None) -> "list[ResourceSnapshot]":
        from sdn_controller.adapters.sql.models import ResourceSnapshotRow
        async with self._session_factory() as session:
            stmt = select(ResourceSnapshotRow)
            if project_id is not None:
                stmt = stmt.where(ResourceSnapshotRow.project_id == project_id)
            stmt = stmt.order_by(ResourceSnapshotRow.version.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.resource_snapshot_from_row(r) for r in rows]

    async def save(self, snap: "ResourceSnapshot") -> None:
        from sdn_controller.adapters.sql.models import ResourceSnapshotRow
        async with self._session_factory() as session:
            existing = await session.get(ResourceSnapshotRow, snap.id)
            if existing is None:
                session.add(mappers.resource_snapshot_to_row(snap))
                await session.commit()

    async def delete(self, snap_id: "ResourceSnapshotId") -> None:
        from sdn_controller.adapters.sql.models import ResourceSnapshotRow
        async with self._session_factory() as session:
            await session.execute(delete(ResourceSnapshotRow).where(ResourceSnapshotRow.id == snap_id))
            await session.commit()


class SqlRetentionPolicyRepository:
    """SQL-репозиторий для RetentionPolicy (N4-05)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, policy_id: "RetentionPolicyId") -> "RetentionPolicy | None":
        from sdn_controller.adapters.sql.models import RetentionPolicyRow
        async with self._session_factory() as session:
            row = await session.get(RetentionPolicyRow, policy_id)
            return mappers.retention_policy_from_row(row) if row is not None else None

    async def get_by_scope(
        self,
        *,
        scope: "RetentionScope",
        project_id: "ProjectId | None" = None,
    ) -> "RetentionPolicy | None":
        from sdn_controller.adapters.sql.models import RetentionPolicyRow
        async with self._session_factory() as session:
            stmt = select(RetentionPolicyRow).where(RetentionPolicyRow.scope == scope.value)
            if project_id is None:
                stmt = stmt.where(RetentionPolicyRow.project_id.is_(None))
            else:
                stmt = stmt.where(RetentionPolicyRow.project_id == project_id)
            row = (await session.scalars(stmt)).first()
            return mappers.retention_policy_from_row(row) if row is not None else None

    async def list(self, *, project_id: "ProjectId | None" = None) -> "list[RetentionPolicy]":
        from sdn_controller.adapters.sql.models import RetentionPolicyRow
        async with self._session_factory() as session:
            stmt = select(RetentionPolicyRow)
            if project_id is not None:
                stmt = stmt.where(RetentionPolicyRow.project_id == project_id)
            stmt = stmt.order_by(RetentionPolicyRow.scope.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.retention_policy_from_row(r) for r in rows]

    async def save(self, policy: "RetentionPolicy") -> None:
        from sdn_controller.adapters.sql.models import RetentionPolicyRow
        async with self._session_factory() as session:
            existing = await session.get(RetentionPolicyRow, policy.id)
            if existing is None:
                session.add(mappers.retention_policy_to_row(policy))
            else:
                existing.retention_days = policy.retention_days
                existing.description = policy.description
                existing.updated_at = policy.updated_at
            await session.commit()

    async def delete(self, policy_id: "RetentionPolicyId") -> None:
        from sdn_controller.adapters.sql.models import RetentionPolicyRow
        async with self._session_factory() as session:
            await session.execute(delete(RetentionPolicyRow).where(RetentionPolicyRow.id == policy_id))
            await session.commit()


class SqlGatewayBondRepository:
    """SQL-репозиторий для GatewayBond (N4-04)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, bond_id: "GatewayBondId") -> "GatewayBond | None":
        from sdn_controller.adapters.sql.models import GatewayBondRow
        async with self._session_factory() as session:
            row = await session.get(GatewayBondRow, bond_id)
            return mappers.gateway_bond_from_row(row) if row is not None else None

    async def list(
        self,
        *,
        node_id: "NodeId | None" = None,
        project_id: "ProjectId | None" = None,
    ) -> "list[GatewayBond]":
        from sdn_controller.adapters.sql.models import GatewayBondRow
        async with self._session_factory() as session:
            stmt = select(GatewayBondRow)
            if node_id is not None:
                stmt = stmt.where(GatewayBondRow.node_id == node_id)
            if project_id is not None:
                stmt = stmt.where(GatewayBondRow.project_id == project_id)
            stmt = stmt.order_by(GatewayBondRow.name.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.gateway_bond_from_row(r) for r in rows]

    async def save(self, bond: "GatewayBond") -> None:
        from sdn_controller.adapters.sql.models import GatewayBondRow
        async with self._session_factory() as session:
            existing = await session.get(GatewayBondRow, bond.id)
            if existing is None:
                session.add(mappers.gateway_bond_to_row(bond))
            else:
                existing.name = bond.name
                existing.mode = bond.mode.value
                existing.members = list(bond.members)
                existing.mtu = bond.mtu
                existing.applied_config = bond.applied_config
                existing.applied_at = bond.applied_at
                existing.labels = dict(bond.labels)
                existing.updated_at = bond.updated_at
            await session.commit()

    async def delete(self, bond_id: "GatewayBondId") -> None:
        from sdn_controller.adapters.sql.models import GatewayBondRow
        async with self._session_factory() as session:
            await session.execute(delete(GatewayBondRow).where(GatewayBondRow.id == bond_id))
            await session.commit()


class SqlLoadBalancerRepository:
    """SQL-репозиторий для LoadBalancer (N4-06)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, lb_id: "LoadBalancerId") -> "LoadBalancer | None":
        from sdn_controller.adapters.sql.models import LoadBalancerRow
        async with self._session_factory() as session:
            row = await session.get(LoadBalancerRow, lb_id)
            return mappers.load_balancer_from_row(row) if row is not None else None

    async def list(self, *, project_id: "ProjectId | None" = None) -> "list[LoadBalancer]":
        from sdn_controller.adapters.sql.models import LoadBalancerRow
        async with self._session_factory() as session:
            stmt = select(LoadBalancerRow)
            if project_id is not None:
                stmt = stmt.where(LoadBalancerRow.project_id == project_id)
            stmt = stmt.order_by(LoadBalancerRow.name.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.load_balancer_from_row(r) for r in rows]

    async def save(self, lb: "LoadBalancer") -> None:
        from sdn_controller.adapters.sql.models import LoadBalancerRow
        async with self._session_factory() as session:
            existing = await session.get(LoadBalancerRow, lb.id)
            if existing is None:
                session.add(mappers.load_balancer_to_row(lb))
            else:
                existing.name = lb.name
                existing.description = lb.description
                existing.status = lb.status.value
                existing.admin_state_up = lb.admin_state_up
                existing.applied_config = lb.applied_config
                existing.applied_at = lb.applied_at
                existing.labels = dict(lb.labels)
                existing.updated_at = lb.updated_at
            await session.commit()

    async def delete(self, lb_id: "LoadBalancerId") -> None:
        from sdn_controller.adapters.sql.models import LoadBalancerRow
        async with self._session_factory() as session:
            await session.execute(delete(LoadBalancerRow).where(LoadBalancerRow.id == lb_id))
            await session.commit()


class SqlLbListenerRepository:
    """SQL-репозиторий для LbListener (N4-06)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, listener_id: "LbListenerId") -> "LbListener | None":
        from sdn_controller.adapters.sql.models import LbListenerRow
        async with self._session_factory() as session:
            row = await session.get(LbListenerRow, listener_id)
            return mappers.lb_listener_from_row(row) if row is not None else None

    async def list(self, *, lb_id: "LoadBalancerId | None" = None) -> "list[LbListener]":
        from sdn_controller.adapters.sql.models import LbListenerRow
        async with self._session_factory() as session:
            stmt = select(LbListenerRow)
            if lb_id is not None:
                stmt = stmt.where(LbListenerRow.lb_id == lb_id)
            stmt = stmt.order_by(LbListenerRow.protocol_port.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.lb_listener_from_row(r) for r in rows]

    async def save(self, listener: "LbListener") -> None:
        from sdn_controller.adapters.sql.models import LbListenerRow
        async with self._session_factory() as session:
            existing = await session.get(LbListenerRow, listener.id)
            if existing is None:
                session.add(mappers.lb_listener_to_row(listener))
            else:
                existing.name = listener.name
                existing.default_pool_id = listener.default_pool_id
                existing.description = listener.description
                existing.labels = dict(listener.labels)
                existing.updated_at = listener.updated_at
            await session.commit()

    async def delete(self, listener_id: "LbListenerId") -> None:
        from sdn_controller.adapters.sql.models import LbListenerRow
        async with self._session_factory() as session:
            await session.execute(delete(LbListenerRow).where(LbListenerRow.id == listener_id))
            await session.commit()


class SqlLbPoolRepository:
    """SQL-репозиторий для LbPool (N4-06)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, pool_id: "LbPoolId") -> "LbPool | None":
        from sdn_controller.adapters.sql.models import LbPoolRow
        async with self._session_factory() as session:
            row = await session.get(LbPoolRow, pool_id)
            return mappers.lb_pool_from_row(row) if row is not None else None

    async def list(self, *, lb_id: "LoadBalancerId | None" = None) -> "list[LbPool]":
        from sdn_controller.adapters.sql.models import LbPoolRow
        async with self._session_factory() as session:
            stmt = select(LbPoolRow)
            if lb_id is not None:
                stmt = stmt.where(LbPoolRow.lb_id == lb_id)
            stmt = stmt.order_by(LbPoolRow.name.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.lb_pool_from_row(r) for r in rows]

    async def save(self, pool: "LbPool") -> None:
        from sdn_controller.adapters.sql.models import LbPoolRow
        async with self._session_factory() as session:
            existing = await session.get(LbPoolRow, pool.id)
            if existing is None:
                session.add(mappers.lb_pool_to_row(pool))
            else:
                existing.name = pool.name
                existing.lb_algorithm = pool.lb_algorithm.value
                existing.session_persistence = pool.session_persistence.value
                existing.description = pool.description
                existing.labels = dict(pool.labels)
                existing.updated_at = pool.updated_at
            await session.commit()

    async def delete(self, pool_id: "LbPoolId") -> None:
        from sdn_controller.adapters.sql.models import LbPoolRow
        async with self._session_factory() as session:
            await session.execute(delete(LbPoolRow).where(LbPoolRow.id == pool_id))
            await session.commit()


class SqlLbMemberRepository:
    """SQL-репозиторий для LbMember (N4-06)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, member_id: "LbMemberId") -> "LbMember | None":
        from sdn_controller.adapters.sql.models import LbMemberRow
        async with self._session_factory() as session:
            row = await session.get(LbMemberRow, member_id)
            return mappers.lb_member_from_row(row) if row is not None else None

    async def list(self, *, pool_id: "LbPoolId | None" = None) -> "list[LbMember]":
        from sdn_controller.adapters.sql.models import LbMemberRow
        async with self._session_factory() as session:
            stmt = select(LbMemberRow)
            if pool_id is not None:
                stmt = stmt.where(LbMemberRow.pool_id == pool_id)
            stmt = stmt.order_by(LbMemberRow.address.asc())
            rows = (await session.scalars(stmt)).all()
            return [mappers.lb_member_from_row(r) for r in rows]

    async def save(self, member: "LbMember") -> None:
        from sdn_controller.adapters.sql.models import LbMemberRow
        async with self._session_factory() as session:
            existing = await session.get(LbMemberRow, member.id)
            if existing is None:
                session.add(mappers.lb_member_to_row(member))
            else:
                existing.weight = member.weight
                existing.admin_state_up = member.admin_state_up
                existing.updated_at = member.updated_at
            await session.commit()

    async def delete(self, member_id: "LbMemberId") -> None:
        from sdn_controller.adapters.sql.models import LbMemberRow
        async with self._session_factory() as session:
            await session.execute(delete(LbMemberRow).where(LbMemberRow.id == member_id))
            await session.commit()


class SqlHealthMonitorRepository:
    """SQL-репозиторий для HealthMonitor (N4-07)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, monitor_id: "HealthMonitorId") -> "HealthMonitor | None":
        from sdn_controller.adapters.sql.models import HealthMonitorRow
        async with self._session_factory() as session:
            row = await session.get(HealthMonitorRow, monitor_id)
            return mappers.health_monitor_from_row(row) if row is not None else None

    async def get_by_pool(self, pool_id: "LbPoolId") -> "HealthMonitor | None":
        from sdn_controller.adapters.sql.models import HealthMonitorRow
        async with self._session_factory() as session:
            stmt = select(HealthMonitorRow).where(HealthMonitorRow.pool_id == pool_id)
            row = (await session.scalars(stmt)).first()
            return mappers.health_monitor_from_row(row) if row is not None else None

    async def save(self, monitor: "HealthMonitor") -> None:
        from sdn_controller.adapters.sql.models import HealthMonitorRow
        async with self._session_factory() as session:
            existing = await session.get(HealthMonitorRow, monitor.id)
            if existing is None:
                session.add(mappers.health_monitor_to_row(monitor))
            else:
                existing.delay = monitor.delay
                existing.timeout = monitor.timeout
                existing.max_retries = monitor.max_retries
                existing.url_path = monitor.url_path
                existing.http_method = monitor.http_method
                existing.expected_codes = monitor.expected_codes
                existing.updated_at = monitor.updated_at
            await session.commit()

    async def delete(self, monitor_id: "HealthMonitorId") -> None:
        from sdn_controller.adapters.sql.models import HealthMonitorRow
        async with self._session_factory() as session:
            await session.execute(delete(HealthMonitorRow).where(HealthMonitorRow.id == monitor_id))
            await session.commit()


# ---------------------------------------------------------------------------
# N5 — Advanced
# ---------------------------------------------------------------------------


class SqlApplyScheduleRepository:
    """SQL-репозиторий для ApplySchedule (N5-01)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, schedule_id: "ApplyScheduleId") -> "ApplySchedule | None":
        from sdn_controller.adapters.sql.models import ApplyScheduleRow
        from sdn_controller.adapters.sql.mappers import apply_schedule_from_row
        async with self._session_factory() as session:
            row = await session.get(ApplyScheduleRow, schedule_id)
            return apply_schedule_from_row(row) if row else None

    async def list(
        self,
        *,
        enabled_only: bool = False,
        project_id: "ProjectId | None" = None,
    ) -> "list[ApplySchedule]":
        from sdn_controller.adapters.sql.models import ApplyScheduleRow
        from sdn_controller.adapters.sql.mappers import apply_schedule_from_row
        async with self._session_factory() as session:
            q = select(ApplyScheduleRow)
            if enabled_only:
                q = q.where(ApplyScheduleRow.enabled.is_(True))
            if project_id is not None:
                q = q.where(ApplyScheduleRow.project_id == project_id)
            rows = (await session.scalars(q)).all()
            return [apply_schedule_from_row(r) for r in rows]

    async def save(self, schedule: "ApplySchedule") -> None:
        from sdn_controller.adapters.sql.models import ApplyScheduleRow
        from sdn_controller.adapters.sql.mappers import apply_schedule_to_row
        async with self._session_factory() as session:
            existing = await session.get(ApplyScheduleRow, schedule.id)
            if existing is None:
                session.add(apply_schedule_to_row(schedule))
            else:
                existing.name = schedule.name
                existing.cron_expr = schedule.cron_expr
                existing.target_type = schedule.target_type.value
                existing.target_id = schedule.target_id
                existing.enabled = schedule.enabled
                existing.project_id = schedule.project_id
                existing.status = schedule.status.value
                existing.last_run_at = schedule.last_run_at
                existing.last_run_status = schedule.last_run_status
                existing.labels = dict(schedule.labels)
                existing.updated_at = schedule.updated_at
            await session.commit()

    async def delete(self, schedule_id: "ApplyScheduleId") -> None:
        from sdn_controller.adapters.sql.models import ApplyScheduleRow
        async with self._session_factory() as session:
            await session.execute(
                delete(ApplyScheduleRow).where(ApplyScheduleRow.id == schedule_id)
            )
            await session.commit()


class SqlMirrorSessionRepository:
    """SQL-репозиторий для MirrorSession (N5-02)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, session_id: "MirrorSessionId") -> "MirrorSession | None":
        from sdn_controller.adapters.sql.models import MirrorSessionRow
        from sdn_controller.adapters.sql.mappers import mirror_session_from_row
        async with self._session_factory() as session:
            row = await session.get(MirrorSessionRow, session_id)
            return mirror_session_from_row(row) if row else None

    async def list(
        self, *, project_id: "ProjectId | None" = None
    ) -> "list[MirrorSession]":
        from sdn_controller.adapters.sql.models import MirrorSessionRow
        from sdn_controller.adapters.sql.mappers import mirror_session_from_row
        async with self._session_factory() as session:
            q = select(MirrorSessionRow)
            if project_id is not None:
                q = q.where(MirrorSessionRow.project_id == project_id)
            rows = (await session.scalars(q)).all()
            return [mirror_session_from_row(r) for r in rows]

    async def save(self, ms: "MirrorSession") -> None:
        from sdn_controller.adapters.sql.models import MirrorSessionRow
        from sdn_controller.adapters.sql.mappers import mirror_session_to_row
        async with self._session_factory() as session:
            existing = await session.get(MirrorSessionRow, ms.id)
            if existing is None:
                session.add(mirror_session_to_row(ms))
            else:
                existing.name = ms.name
                existing.source_port_id = ms.source_port_id
                existing.direction = ms.direction.value
                existing.destination_port_id = ms.destination_port_id
                existing.destination_ip = ms.destination_ip
                existing.filter_vlan = ms.filter_vlan
                existing.project_id = ms.project_id
                existing.status = ms.status.value
                existing.applied_config = ms.applied_config
                existing.applied_at = ms.applied_at
                existing.labels = dict(ms.labels)
                existing.updated_at = ms.updated_at
            await session.commit()

    async def delete(self, session_id: "MirrorSessionId") -> None:
        from sdn_controller.adapters.sql.models import MirrorSessionRow
        async with self._session_factory() as session:
            await session.execute(
                delete(MirrorSessionRow).where(MirrorSessionRow.id == session_id)
            )
            await session.commit()


class SqlVpnTunnelRepository:
    """SQL-репозиторий для VpnTunnel (N5-05)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, tunnel_id: "VpnTunnelId") -> "VpnTunnel | None":
        from sdn_controller.adapters.sql.models import VpnTunnelRow
        from sdn_controller.adapters.sql.mappers import vpn_tunnel_from_row
        async with self._session_factory() as session:
            row = await session.get(VpnTunnelRow, tunnel_id)
            return vpn_tunnel_from_row(row) if row else None

    async def list(
        self, *, project_id: "ProjectId | None" = None
    ) -> "list[VpnTunnel]":
        from sdn_controller.adapters.sql.models import VpnTunnelRow
        from sdn_controller.adapters.sql.mappers import vpn_tunnel_from_row
        async with self._session_factory() as session:
            q = select(VpnTunnelRow)
            if project_id is not None:
                q = q.where(VpnTunnelRow.project_id == project_id)
            rows = (await session.scalars(q)).all()
            return [vpn_tunnel_from_row(r) for r in rows]

    async def save(self, tunnel: "VpnTunnel") -> None:
        from sdn_controller.adapters.sql.models import VpnTunnelRow
        from sdn_controller.adapters.sql.mappers import vpn_tunnel_to_row
        async with self._session_factory() as session:
            existing = await session.get(VpnTunnelRow, tunnel.id)
            if existing is None:
                session.add(vpn_tunnel_to_row(tunnel))
            else:
                existing.name = tunnel.name
                existing.protocol = tunnel.protocol.value
                existing.local_endpoint = tunnel.local_endpoint
                existing.remote_endpoint = tunnel.remote_endpoint
                existing.local_public_key = tunnel.local_public_key
                existing.remote_public_key = tunnel.remote_public_key
                existing.listen_port = tunnel.listen_port
                existing.preshared_key = tunnel.preshared_key
                existing.project_id = tunnel.project_id
                existing.status = tunnel.status.value
                existing.applied_config = tunnel.applied_config
                existing.applied_at = tunnel.applied_at
                existing.labels = dict(tunnel.labels)
                existing.updated_at = tunnel.updated_at
            await session.commit()

    async def delete(self, tunnel_id: "VpnTunnelId") -> None:
        from sdn_controller.adapters.sql.models import VpnTunnelRow
        async with self._session_factory() as session:
            await session.execute(
                delete(VpnTunnelRow).where(VpnTunnelRow.id == tunnel_id)
            )
            await session.commit()


class SqlVpnPeerRepository:
    """SQL-репозиторий для VpnPeer (N5-05)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, peer_id: "VpnPeerId") -> "VpnPeer | None":
        from sdn_controller.adapters.sql.models import VpnPeerRow
        from sdn_controller.adapters.sql.mappers import vpn_peer_from_row
        async with self._session_factory() as session:
            row = await session.get(VpnPeerRow, peer_id)
            return vpn_peer_from_row(row) if row else None

    async def list(
        self, *, tunnel_id: "VpnTunnelId | None" = None
    ) -> "list[VpnPeer]":
        from sdn_controller.adapters.sql.models import VpnPeerRow
        from sdn_controller.adapters.sql.mappers import vpn_peer_from_row
        async with self._session_factory() as session:
            q = select(VpnPeerRow)
            if tunnel_id is not None:
                q = q.where(VpnPeerRow.tunnel_id == tunnel_id)
            rows = (await session.scalars(q)).all()
            return [vpn_peer_from_row(r) for r in rows]

    async def save(self, peer: "VpnPeer") -> None:
        from sdn_controller.adapters.sql.models import VpnPeerRow
        from sdn_controller.adapters.sql.mappers import vpn_peer_to_row
        async with self._session_factory() as session:
            existing = await session.get(VpnPeerRow, peer.id)
            if existing is None:
                session.add(vpn_peer_to_row(peer))
            else:
                existing.public_key = peer.public_key
                existing.endpoint = peer.endpoint
                existing.allowed_ips = list(peer.allowed_ips)
                existing.persistent_keepalive = peer.persistent_keepalive
                existing.updated_at = peer.updated_at
            await session.commit()

    async def delete(self, peer_id: "VpnPeerId") -> None:
        from sdn_controller.adapters.sql.models import VpnPeerRow
        async with self._session_factory() as session:
            await session.execute(
                delete(VpnPeerRow).where(VpnPeerRow.id == peer_id)
            )
            await session.commit()
