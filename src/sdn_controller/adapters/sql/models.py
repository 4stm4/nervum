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

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, TypeDecorator, UniqueConstraint
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
    # N1-06: maintenance mode
    maintenance: Mapped[bool] = mapped_column(
        "maintenance", nullable=False, default=False
    )
    maintenance_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
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


# ---------------------------------------------------------------------------
# N1 models
# ---------------------------------------------------------------------------


class LogicalPortRow(Base):
    """Logical port — binding between a VIF on a Node and a Network (N1-01)."""

    __tablename__ = "logical_ports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    node_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    network_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("networks.id", ondelete="CASCADE"),
        nullable=False,
    )
    vif_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (
        Index("ix_logical_ports_node_id", "node_id"),
        Index("ix_logical_ports_network_id", "network_id"),
        Index("ix_logical_ports_project_id", "project_id"),
    )


class SecurityGroupRow(Base):
    """Security group — named set of ports / addresses (N1-02)."""

    __tablename__ = "security_groups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    members: Mapped[list[SecurityGroupMemberRow]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (Index("ix_security_groups_project_id", "project_id"),)


class SecurityGroupMemberRow(Base):
    """One membership entry (sg_id, type, value) with a composite PK."""

    __tablename__ = "security_group_members"

    sg_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("security_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    member_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    member_value: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    group: Mapped[SecurityGroupRow] = relationship(back_populates="members")

    __table_args__ = (Index("ix_sg_members_sg_id", "sg_id"),)


class AddressPoolRow(Base):
    """Named set of CIDRs (N1-03)."""

    __tablename__ = "address_pools"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cidrs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (Index("ix_address_pools_project_id", "project_id"),)


class ServiceObjectRow(Base):
    """Named protocol/port definition (N1-04)."""

    __tablename__ = "service_objects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False)
    ports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (Index("ix_service_objects_project_id", "project_id"),)


class QosPolicyRow(Base):
    """Bandwidth / DSCP marking policy (N1-05)."""

    __tablename__ = "qos_policies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ingress_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    egress_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    burst_kb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dscp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (Index("ix_qos_policies_project_id", "project_id"),)


# ---------------------------------------------------------------------------
# N2 — SecurityPolicy + TrunkPort
# ---------------------------------------------------------------------------


class SecurityPolicyRow(Base):
    """Политика безопасности (N2-01, N2-03)."""

    __tablename__ = "security_policies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    # Скомпилированный nftables-скрипт (N2-02), Text для длинных скриптов
    compiled_ruleset: Mapped[str | None] = mapped_column(Text, nullable=True)
    compiled_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    rules: Mapped[list["SecurityPolicyRuleRow"]] = relationship(
        back_populates="policy",
        cascade="all, delete-orphan",
        order_by="SecurityPolicyRuleRow.priority",
    )

    __table_args__ = (Index("ix_security_policies_project_id", "project_id"),)


class SecurityPolicyRuleRow(Base):
    """Одно правило внутри SecurityPolicy (N2-01, N2-04).

    Счётчики packet_count/byte_count обновляются верификатором (N2-04).
    """

    __tablename__ = "security_policy_rules"

    policy_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("security_policies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rule_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="any")
    source_value: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    destination_type: Mapped[str] = mapped_column(String(32), nullable=False, default="any")
    destination_value: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    service_object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    comment: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # Счётчики пакетов и байт (N2-04)
    packet_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    policy: Mapped[SecurityPolicyRow] = relationship(back_populates="rules")

    __table_args__ = (Index("ix_spol_rules_policy_id", "policy_id"),)


class TrunkPortRow(Base):
    """Транковый порт 802.1q (N2-05)."""

    __tablename__ = "trunk_ports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    node_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    logical_port_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # vlan_ids хранится как JSON-массив целых чисел
    vlan_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    native_vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (
        Index("ix_trunk_ports_node_id", "node_id"),
        Index("ix_trunk_ports_project_id", "project_id"),
    )


# ---------------------------------------------------------------------------
# N3 — Router, FloatingIP, BgpPeer
# ---------------------------------------------------------------------------


class RouterRow(Base):
    """L3-маршрутизатор (N3-01)."""

    __tablename__ = "routers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_network_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # frozenset[NetworkId] хранится как отсортированный JSON-массив
    internal_network_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # tuple[StaticRoute, ...] хранится как JSON-массив [{destination, nexthop}]
    static_routes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="build")
    admin_state_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ha_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    vrrp_priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vrrp_vrid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # IPv6Config хранится как JSON-dict или None
    ipv6_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    applied_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    bgp_peers: Mapped[list["BgpPeerRow"]] = relationship(
        "BgpPeerRow",
        back_populates="router",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_routers_project_id", "project_id"),
        Index("ix_routers_status", "status"),
    )


class FloatingIpRow(Base):
    """Floating IP (N3-02)."""

    __tablename__ = "floating_ips"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_network_id: Mapped[str] = mapped_column(String(64), nullable=False)
    floating_ip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fixed_ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    logical_port_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    router_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("routers.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="down")
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (
        Index("ix_floating_ips_project_id", "project_id"),
        Index("ix_floating_ips_router_id", "router_id"),
        Index("ix_floating_ips_floating_ip", "floating_ip_address"),
    )


class BgpPeerRow(Base):
    """BGP-пир маршрутизатора (N3-05)."""

    __tablename__ = "bgp_peers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    router_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("routers.id", ondelete="CASCADE"),
        nullable=False,
    )
    peer_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    peer_asn: Mapped[int] = mapped_column(Integer, nullable=False)
    local_asn: Mapped[int] = mapped_column(Integer, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    router: Mapped[RouterRow] = relationship(back_populates="bgp_peers")

    __table_args__ = (
        Index("ix_bgp_peers_router_id", "router_id"),
        Index("ix_bgp_peers_project_id", "project_id"),
    )


# ---------------------------------------------------------------------------
# N4 — Governance & Scale
# ---------------------------------------------------------------------------


class ProjectQuotaRow(Base):
    """Квоты ресурсов проекта (N4-01)."""

    __tablename__ = "project_quotas"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # словарь resource_type → int | null, хранится как JSON
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (
        Index("ix_project_quotas_project_id", "project_id"),
    )


class ResourceSnapshotRow(Base):
    """Мультиресурсный версионированный снапшот (N4-03)."""

    __tablename__ = "resource_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    resource_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (
        Index("ix_resource_snapshots_project_id", "project_id"),
        Index("ix_resource_snapshots_version", "project_id", "version"),
    )


class RetentionPolicyRow(Base):
    """Политика хранения данных (N4-05)."""

    __tablename__ = "retention_policies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (
        Index("ix_retention_policies_scope", "scope"),
        Index("ix_retention_policies_project_id", "project_id"),
        UniqueConstraint("scope", "project_id", name="uq_retention_scope_project"),
    )


class GatewayBondRow(Base):
    """Bond-интерфейс Gateway HA (N4-04)."""

    __tablename__ = "gateway_bonds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    bond_name: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    members: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    mtu: Mapped[int] = mapped_column(Integer, nullable=False, default=1500)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applied_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    __table_args__ = (
        Index("ix_gateway_bonds_node_id", "node_id"),
        Index("ix_gateway_bonds_project_id", "project_id"),
    )


class LoadBalancerRow(Base):
    """Балансировщик нагрузки (N4-06)."""

    __tablename__ = "load_balancers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    vip_address: Mapped[str] = mapped_column(String(64), nullable=False)
    vip_network_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    router_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="haproxy")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="build")
    admin_state_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    applied_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    listeners: Mapped[list["LbListenerRow"]] = relationship(
        "LbListenerRow",
        back_populates="load_balancer",
        cascade="all, delete-orphan",
    )
    pools: Mapped[list["LbPoolRow"]] = relationship(
        "LbPoolRow",
        back_populates="load_balancer",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_load_balancers_project_id", "project_id"),
        Index("ix_load_balancers_status", "status"),
    )


class LbListenerRow(Base):
    """Listener балансировщика (N4-06)."""

    __tablename__ = "lb_listeners"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    lb_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("load_balancers.id", ondelete="CASCADE"),
        nullable=False,
    )
    protocol: Mapped[str] = mapped_column(String(16), nullable=False)
    protocol_port: Mapped[int] = mapped_column(Integer, nullable=False)
    default_pool_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    load_balancer: Mapped[LoadBalancerRow] = relationship(back_populates="listeners")

    __table_args__ = (
        Index("ix_lb_listeners_lb_id", "lb_id"),
    )


class LbPoolRow(Base):
    """Пул бэкендов балансировщика (N4-06)."""

    __tablename__ = "lb_pools"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    lb_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("load_balancers.id", ondelete="CASCADE"),
        nullable=False,
    )
    protocol: Mapped[str] = mapped_column(String(16), nullable=False)
    lb_algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="round_robin")
    session_persistence: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    load_balancer: Mapped[LoadBalancerRow] = relationship(back_populates="pools")
    members: Mapped[list["LbMemberRow"]] = relationship(
        "LbMemberRow",
        back_populates="pool",
        cascade="all, delete-orphan",
    )
    health_monitor: Mapped["HealthMonitorRow | None"] = relationship(
        "HealthMonitorRow",
        back_populates="pool",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        Index("ix_lb_pools_lb_id", "lb_id"),
    )


class LbMemberRow(Base):
    """Участник пула балансировщика (N4-06)."""

    __tablename__ = "lb_members"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pool_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("lb_pools.id", ondelete="CASCADE"),
        nullable=False,
    )
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol_port: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    admin_state_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    pool: Mapped[LbPoolRow] = relationship(back_populates="members")

    __table_args__ = (
        Index("ix_lb_members_pool_id", "pool_id"),
    )


class HealthMonitorRow(Base):
    """Health monitor пула балансировщика (N4-07)."""

    __tablename__ = "health_monitors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pool_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("lb_pools.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    check_type: Mapped[str] = mapped_column(String(16), nullable=False)
    delay: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    url_path: Mapped[str] = mapped_column(String(256), nullable=False, default="/health")
    http_method: Mapped[str] = mapped_column(String(8), nullable=False, default="GET")
    expected_codes: Mapped[str] = mapped_column(String(32), nullable=False, default="200")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)

    pool: Mapped[LbPoolRow] = relationship(back_populates="health_monitor")
