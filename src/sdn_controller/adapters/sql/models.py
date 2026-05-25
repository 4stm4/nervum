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

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, TypeDecorator, UniqueConstraint
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


class ProjectRow(Base):
    """Проект — верхнеуровневая изоляция ресурсов (N0)."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    members: Mapped[list[ProjectMemberRow]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (Index("ix_projects_slug", "slug"),)


class ProjectMemberRow(Base):
    """Привязка сервисного аккаунта к проекту с ролью (N0)."""

    __tablename__ = "project_members"

    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    service_account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    project: Mapped[ProjectRow] = relationship(back_populates="members")

    __table_args__ = (
        Index("ix_project_members_sa", "service_account_id"),
    )


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
    # M9: pinned thumbprint серверного TLS-сертификата агента (SHA-256 hex).
    tls_thumbprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # N0: project scope (nullable — existing nodes without a project stay global)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    # M7: edge-service intent stored as JSON blobs. Both nullable — most
    # networks won't carry NAT or a firewall policy.
    nat: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    firewall_policy: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # N0: project scope
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    # M6: IPAM extras. Pools/reserved each stored as JSON arrays of
    # ``{"start": "...", "end": "..."}``; DNS servers as a flat list.
    dns_servers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allocation_pools: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    reserved_ranges: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    # M7: subnet-scoped edge-service intent.
    dhcp: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    dns_zone: Mapped[str | None] = mapped_column(String(253), nullable=True)

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


class OperationLockRow(Base):
    """Распределённый advisory-lock (M13 — SDN-037).

    Атомарность через UNIQUE на ``key``: первый INSERT успешный, второй
    падает с ``IntegrityError`` — ``SqlLockStore`` это перехватывает.

    TTL хранится в ``expires_at``: просроченные локи отстреливаются на
    следующей попытке ``try_lock`` (cleanup в той же транзакции, что
    INSERT).
    """

    __tablename__ = "operation_locks"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (Index("ix_operation_locks_expires_at", "expires_at"),)


class NodeSnapshotRow(Base):
    """Каталог снапшотов узлов (M11 — SDN-035).

    Сами байты снапшота живут на агенте (по ``agent_snapshot_id``); тут
    мы храним только указатель, чтобы CLI/UI имели каталог независимо
    от доступности агента.
    """

    __tablename__ = "node_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (Index("ix_node_snapshots_node_id_created", "node_id", "created_at"),)


class AuditEventRow(Base):
    """Immutable journal of administrative actions (M10 — SDN-033).

    Только INSERT и SELECT. UPDATE/DELETE — это уже компрометация
    аудита, и реализация репозитория не предоставляет таких методов.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    __table_args__ = (
        # Лента отсортирована по времени; фильтры по actor/action/resource
        # — горячие, поэтому индексируем.
        Index("ix_audit_events_at", "at"),
        Index("ix_audit_events_actor", "actor"),
        Index("ix_audit_events_action", "action"),
        Index("ix_audit_events_resource", "resource_type", "resource_id"),
    )


class ServiceAccountRow(Base):
    """Сервисная учётка northbound API (M9)."""

    __tablename__ = "service_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)

    tokens: Mapped[list[ServiceTokenRow]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ServiceTokenRow(Base):
    """Один токен сервисной учётки (хранится только хэш)."""

    __tablename__ = "service_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    service_account_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("service_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SHA-256 hex; UNIQUE — это горячий путь аутентификации.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    issued_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    account: Mapped[ServiceAccountRow] = relationship(back_populates="tokens")

    __table_args__ = (Index("ix_service_tokens_account", "service_account_id"),)


class IpAllocationRow(Base):
    """One row per leased IP address.

    ``(subnet_id, ip_address)`` is unique — two allocations can't share an
    address. ``owner_type``/``owner_id`` flatten the ``OwnerRef`` so we can
    filter cheaply (``WHERE owner_type=? AND owner_id=?``).
    """

    __tablename__ = "ip_allocations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subnet_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("subnets.id", ondelete="CASCADE"),
        nullable=False,
    )
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("subnet_id", "ip_address", name="uq_ip_allocations_subnet_ip"),
        Index("ix_ip_allocations_subnet_id", "subnet_id"),
        Index("ix_ip_allocations_owner", "owner_type", "owner_id"),
    )


class OutboxEventRow(Base):
    """Transactional outbox (M13 — SDN-055).

    ``event_id`` — autoincrement integer, монотонно возрастающий и
    устойчивый к рестартам. Именно его подписчик использует как
    watermark («дай мне всё, что > X»). ``id`` — Stripe-style строка
    для логов и API, ``event_id`` — для упорядочивания.

    ``delivered_at`` выставляется retention-job'ом ровно после того,
    как событие принято всеми активными подписками; до этого момента
    оно visible снапшоту/list_undelivered.
    """

    __tablename__ = "outbox_events"

    # ``event_id`` is the watermark used by subscribers — it must be
    # monotonic, so we make it the autoincrement PK. The Stripe-style
    # string ``id`` is kept as a unique column for log readability.
    event_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    delivered_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    # N0-04: envelope v2
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_outbox_events_id", "id"),
        Index("ix_outbox_events_delivered_at", "delivered_at"),
    )


class WebhookSubscriptionRow(Base):
    """Webhook subscription (M13 — SDN-054).

    Только метаданные подписки + cursor доставки. История попыток
    delivery'я выходит за рамки текущего scope'а — для аудита достаточно
    ``last_delivery_at``/``last_delivery_status``.
    """

    __tablename__ = "webhook_subscriptions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_delivery_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_delivery_status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_webhook_subscriptions_state", "state"),)
