"""Unit-тесты для outbox-репозиториев и ``EventPublisher`` (SDN-055)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sdn_controller.adapters.memory import InMemoryOutboxRepository
from sdn_controller.adapters.sql import SqlOutboxRepository
from sdn_controller.adapters.sql.models import Base
from sdn_controller.core.entities import OutboxEvent
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.value_objects.ids import OutboxEventId
from tests.conftest import CountingIdFactory, FrozenClock

_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


def _event(name: str = "outbox_x", *, event_id: int = 0) -> OutboxEvent:
    return OutboxEvent(
        id=OutboxEventId(name),
        event_id=event_id,
        occurred_at=_NOW,
        event_type="network.created",
        resource_type="network",
        resource_id="net_1",
        payload={"name": "tenant-a"},
    )


# ---------------------------------------------------------------------------
# InMemoryOutboxRepository
# ---------------------------------------------------------------------------


async def test_in_memory_append_assigns_monotonic_event_id() -> None:
    repo = InMemoryOutboxRepository()
    a = await repo.append(_event("a"))
    b = await repo.append(_event("b"))
    c = await repo.append(_event("c"))
    assert (a.event_id, b.event_id, c.event_id) == (1, 2, 3)


async def test_in_memory_list_since_returns_only_newer() -> None:
    repo = InMemoryOutboxRepository()
    await repo.append(_event("a"))
    b = await repo.append(_event("b"))
    c = await repo.append(_event("c"))

    items = await repo.list_since(since=b.event_id)
    assert [e.id for e in items] == [c.id]


async def test_in_memory_list_undelivered_filters_delivered() -> None:
    repo = InMemoryOutboxRepository()
    a = await repo.append(_event("a"))
    b = await repo.append(_event("b"))
    await repo.mark_delivered([a.id], at=_NOW)
    items = await repo.list_undelivered()
    assert [e.id for e in items] == [b.id]


async def test_in_memory_head_event_id_returns_latest() -> None:
    repo = InMemoryOutboxRepository()
    assert await repo.head_event_id() == 0
    await repo.append(_event("a"))
    await repo.append(_event("b"))
    assert await repo.head_event_id() == 2


async def test_in_memory_delete_delivered_before_cutoff() -> None:
    repo = InMemoryOutboxRepository()
    a = await repo.append(_event("a"))
    b = await repo.append(_event("b"))
    await repo.mark_delivered([a.id, b.id], at=_NOW - timedelta(days=10))
    # b всё ещё свежее cutoff'а — не удаляется.
    deleted = await repo.delete_delivered_before(_NOW - timedelta(days=5))
    assert deleted == 2  # оба удалятся, т.к. delivered_at одинаков
    assert await repo.get(a.id) is None
    assert await repo.get(b.id) is None


# ---------------------------------------------------------------------------
# SqlOutboxRepository — отдельный engine per-test.
# ---------------------------------------------------------------------------


@pytest.fixture
async def sql_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_sql_append_assigns_monotonic_event_id(
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlOutboxRepository(sql_session_factory)
    a = await repo.append(_event("a"))
    b = await repo.append(_event("b"))
    assert a.event_id < b.event_id  # autoincrement, не обязательно 1/2


async def test_sql_list_since_returns_only_newer(
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlOutboxRepository(sql_session_factory)
    a = await repo.append(_event("a"))
    b = await repo.append(_event("b"))
    items = await repo.list_since(since=a.event_id)
    assert [e.id for e in items] == [b.id]


async def test_sql_mark_delivered_updates_only_pending(
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlOutboxRepository(sql_session_factory)
    a = await repo.append(_event("a"))
    b = await repo.append(_event("b"))
    await repo.mark_delivered([a.id], at=_NOW)

    remaining = await repo.list_undelivered()
    assert [e.id for e in remaining] == [b.id]


async def test_sql_head_event_id(
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repo = SqlOutboxRepository(sql_session_factory)
    assert await repo.head_event_id() == 0
    a = await repo.append(_event("a"))
    head = await repo.head_event_id()
    assert head == a.event_id


# ---------------------------------------------------------------------------
# EventPublisher
# ---------------------------------------------------------------------------


async def test_publisher_appends_event_with_clock_and_id(
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> None:
    repo = InMemoryOutboxRepository()
    publisher = EventPublisher(outbox=repo, clock=clock, ids=ids)
    event = await publisher.publish(
        event_type="network.created",
        resource_type="network",
        resource_id="net_42",
        payload={"vni": 10100},
    )

    assert event.event_id == 1
    assert event.occurred_at == clock.current
    assert event.event_type == "network.created"
    assert event.payload == {"vni": 10100}
    assert event.id.startswith("outbox_")


async def test_publisher_rejects_invalid_event_type(
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> None:
    repo = InMemoryOutboxRepository()
    publisher = EventPublisher(outbox=repo, clock=clock, ids=ids)
    with pytest.raises(Exception, match="must be"):
        await publisher.publish(
            event_type="invalid_no_dot",
            resource_type="network",
            resource_id=None,
        )
