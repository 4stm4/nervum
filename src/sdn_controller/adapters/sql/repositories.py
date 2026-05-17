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

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdn_controller.adapters.sql import mappers, models
from sdn_controller.adapters.sql.models import (
    NetworkRow,
    NodeRow,
    OperationEventRow,
    OperationRow,
)
from sdn_controller.core.entities import (
    Network,
    Node,
    Operation,
    OperationEvent,
)
from sdn_controller.core.value_objects.enums import OperationStatus
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import NetworkId, NodeId, OperationId


class SqlNodeRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, node_id: NodeId) -> Node | None:
        async with self._session_factory() as session:
            row = await session.get(NodeRow, node_id)
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
    row.created_at = network.created_at
    row.updated_at = network.updated_at
    if network.subnet is None:
        row.subnet = None
    else:
        row.subnet = models.SubnetRow(
            id=network.subnet.id,
            network_id=network.id,
            cidr=network.subnet.cidr,
            gateway=network.subnet.gateway,
        )


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
