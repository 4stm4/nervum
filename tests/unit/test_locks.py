"""Unit-тесты для ``InMemoryLockStore`` и ``SqlLockStore`` (SDN-037)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sdn_controller.adapters.locks import InMemoryLockStore, SqlLockStore
from sdn_controller.adapters.sql.models import Base
from tests.conftest import FrozenClock

# ---------------------------------------------------------------------------
# InMemoryLockStore
# ---------------------------------------------------------------------------


async def test_in_memory_try_lock_succeeds_for_new_key(clock: FrozenClock) -> None:
    store = InMemoryLockStore(clock=clock)
    assert await store.try_lock("k", owner="op_1", ttl_seconds=60) is True


async def test_in_memory_try_lock_blocks_other_owner(clock: FrozenClock) -> None:
    store = InMemoryLockStore(clock=clock)
    await store.try_lock("k", owner="op_1", ttl_seconds=60)
    assert await store.try_lock("k", owner="op_2", ttl_seconds=60) is False


async def test_in_memory_try_lock_allows_same_owner_reacquire(clock: FrozenClock) -> None:
    store = InMemoryLockStore(clock=clock)
    assert await store.try_lock("k", owner="op_1", ttl_seconds=60) is True
    assert await store.try_lock("k", owner="op_1", ttl_seconds=60) is True


async def test_in_memory_release_frees_lock(clock: FrozenClock) -> None:
    store = InMemoryLockStore(clock=clock)
    await store.try_lock("k", owner="op_1", ttl_seconds=60)
    await store.release("k", owner="op_1")
    assert await store.try_lock("k", owner="op_2", ttl_seconds=60) is True


async def test_in_memory_release_by_wrong_owner_is_noop(clock: FrozenClock) -> None:
    store = InMemoryLockStore(clock=clock)
    await store.try_lock("k", owner="op_1", ttl_seconds=60)
    await store.release("k", owner="op_2")  # wrong owner
    assert await store.try_lock("k", owner="op_3", ttl_seconds=60) is False
    assert await store.current_owner("k") == "op_1"


async def test_in_memory_ttl_expiry_allows_reacquire(clock: FrozenClock) -> None:
    store = InMemoryLockStore(clock=clock)
    await store.try_lock("k", owner="op_1", ttl_seconds=60)
    clock.advance(61)
    assert await store.try_lock("k", owner="op_2", ttl_seconds=60) is True
    assert await store.current_owner("k") == "op_2"


async def test_in_memory_current_owner_returns_none_for_unknown(clock: FrozenClock) -> None:
    store = InMemoryLockStore(clock=clock)
    assert await store.current_owner("missing") is None


async def test_in_memory_current_owner_returns_none_after_expiry(clock: FrozenClock) -> None:
    store = InMemoryLockStore(clock=clock)
    await store.try_lock("k", owner="op_1", ttl_seconds=60)
    clock.advance(61)
    assert await store.current_owner("k") is None


# ---------------------------------------------------------------------------
# SqlLockStore — отдельная схема per-test, чтобы запуски не делили строки.
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


async def test_sql_try_lock_succeeds_for_new_key(
    clock: FrozenClock,
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlLockStore(sql_session_factory, clock=clock)
    assert await store.try_lock("k", owner="op_1", ttl_seconds=60) is True


async def test_sql_try_lock_blocks_other_owner(
    clock: FrozenClock,
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlLockStore(sql_session_factory, clock=clock)
    await store.try_lock("k", owner="op_1", ttl_seconds=60)
    assert await store.try_lock("k", owner="op_2", ttl_seconds=60) is False


async def test_sql_try_lock_allows_same_owner_reacquire(
    clock: FrozenClock,
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlLockStore(sql_session_factory, clock=clock)
    assert await store.try_lock("k", owner="op_1", ttl_seconds=60) is True
    assert await store.try_lock("k", owner="op_1", ttl_seconds=60) is True


async def test_sql_release_frees_lock(
    clock: FrozenClock,
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlLockStore(sql_session_factory, clock=clock)
    await store.try_lock("k", owner="op_1", ttl_seconds=60)
    await store.release("k", owner="op_1")
    assert await store.try_lock("k", owner="op_2", ttl_seconds=60) is True


async def test_sql_release_by_wrong_owner_is_noop(
    clock: FrozenClock,
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlLockStore(sql_session_factory, clock=clock)
    await store.try_lock("k", owner="op_1", ttl_seconds=60)
    await store.release("k", owner="op_2")  # wrong owner
    assert await store.current_owner("k") == "op_1"


async def test_sql_ttl_expiry_allows_reacquire(
    clock: FrozenClock,
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlLockStore(sql_session_factory, clock=clock)
    await store.try_lock("k", owner="op_1", ttl_seconds=60)
    clock.advance(61)
    assert await store.try_lock("k", owner="op_2", ttl_seconds=60) is True
    assert await store.current_owner("k") == "op_2"


async def test_sql_current_owner_returns_none_after_expiry(
    clock: FrozenClock,
    sql_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = SqlLockStore(sql_session_factory, clock=clock)
    await store.try_lock("k", owner="op_1", ttl_seconds=60)
    clock.advance(61)
    assert await store.current_owner("k") is None
