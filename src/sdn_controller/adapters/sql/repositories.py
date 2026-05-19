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
    AuditEventRow,
    EnrollmentTokenRow,
    IpAllocationRow,
    NetworkRow,
    NodeRow,
    NodeSnapshotRow,
    ObservedStateRow,
    OperationEventRow,
    OperationRow,
    OutboxEventRow,
    ServiceAccountRow,
    ServiceTokenRow,
    SubnetRow,
)
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
    OutboxEvent,
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
    OutboxEventId,
    ServiceAccountId,
    ServiceTokenId,
    SubnetId,
)
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

    async def list_since(
        self, *, since: int = 0, limit: int = 200
    ) -> Sequence[OutboxEvent]:
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

    async def mark_delivered(
        self, event_ids: Sequence[OutboxEventId], *, at: datetime
    ) -> None:
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
