"""SQLAlchemy ORM models.

Models live in the adapter — the core never imports them. They mirror the
shape of the domain aggregates without being identical to them:

* ``OperationRow`` stores the flattened operation header; child events live in
  ``OperationEventRow`` and are loaded eagerly through the relationship.
* ``SubnetRow`` is a separate table even though every ``Network`` currently
  owns at most one subnet — keeping it normalised makes the move to
  multi-subnet networks a pure schema change later, with no row reshaping.

JSON-shaped columns (``labels``, ``roles``, ``payload``, ``error_details``) use
SQLAlchemy's portable ``JSON`` type. On SQLite this is TEXT with a JSON
encoder; on PostgreSQL it is ``JSONB`` automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, TypeDecorator
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime


class Base(DeclarativeBase):
    """Shared declarative base. ``Base.metadata`` drives Alembic."""


# ---------------------------------------------------------------------------
# UTC-aware datetime
# ---------------------------------------------------------------------------


class UtcDateTime(TypeDecorator[datetime]):
    """Always store and return tz-aware UTC datetimes.

    SQLite has no native datetime type — values round-trip as ISO strings and
    SQLAlchemy returns them tz-naive. We attach ``UTC`` on read and refuse
    naive values on write so the domain never sees a bare datetime.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed; pass tz-aware (UTC) values")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class NodeRow(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    mgmt_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    # ``capabilities`` is a flat JSON blob:
    #   { "ovs_version": "3.2.1", "kernel": "6.6", "interfaces": [...], "features": [...] }
    # Stored as JSON instead of separate columns because the shape is read-as-a-whole
    # and we don't query into it.
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    enrollment_tokens: Mapped[list[EnrollmentTokenRow]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class NetworkRow(Base):
    __tablename__ = "networks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    mtu: Mapped[int] = mapped_column(Integer, nullable=False, default=1500)
    vlan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vni: Mapped[int | None] = mapped_column(Integer, nullable=True)
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    intent_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # M5: which nodes carry this network + the canonical spec hash
    node_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    subnet: Mapped[SubnetRow | None] = relationship(
        back_populates="network",
        cascade="all, delete-orphan",
        single_parent=True,
        uselist=False,
        lazy="selectin",
    )


class SubnetRow(Base):
    __tablename__ = "subnets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    network_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("networks.id", ondelete="CASCADE"),
        unique=True,  # 1:1 today; drop the constraint to allow many-per-network later.
        nullable=False,
    )
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    gateway: Mapped[str | None] = mapped_column(String(64), nullable=True)

    network: Mapped[NetworkRow] = relationship(back_populates="subnet")


class OperationRow(Base):
    __tablename__ = "operations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    events: Mapped[list[OperationEventRow]] = relationship(
        back_populates="operation",
        cascade="all, delete-orphan",
        order_by="OperationEventRow.sequence",
        lazy="selectin",
    )

    __table_args__ = (
        # Listing endpoint orders by created_at DESC; this composite index
        # serves both the order-by and the id tie-breaker.
        Index("ix_operations_created_at_id", "created_at", "id"),
    )


class OperationEventRow(Base):
    __tablename__ = "operation_events"

    operation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("operations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    operation: Mapped[OperationRow] = relationship(back_populates="events")


class EnrollmentTokenRow(Base):
    __tablename__ = "enrollment_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SHA-256 hex; UNIQUE so an attacker can't hide two pre-images that hash
    # to the same row, and so ``get_by_hash`` becomes an index seek.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    issued_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    node: Mapped[NodeRow] = relationship(back_populates="enrollment_tokens")

    __table_args__ = (Index("ix_enrollment_tokens_node_id", "node_id"),)


class ObservedStateRow(Base):
    """Per-node cache of the last OVS state observed by the controller.

    One row per node (``node_id`` is the PK). The ``payload`` blob holds the
    full ``ObservedState`` as JSON — we don't query into it, so a single
    column is cheaper than a 3-table normalised schema.
    """

    __tablename__ = "observed_states"

    node_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    observed_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
