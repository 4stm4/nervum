"""Async engine and session-factory builders.

We register two SQLite-specific PRAGMAs on every new connection so the database
behaves the way the application expects, regardless of the operator's defaults:

* ``foreign_keys=ON`` — SQLite ships with foreign-key enforcement disabled. We
  rely on cascading deletes from ``networks → subnets`` and
  ``operations → operation_events``, so this PRAGMA is mandatory.
* ``journal_mode=WAL`` — write-ahead logging gives readers and a single writer
  proper concurrency, which matches FastAPI's threading model. It is also the
  PRAGMA the SQLite team recommends for app workloads.

For non-SQLite backends (PostgreSQL) the listener is a no-op.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry, StaticPool


def build_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an ``AsyncEngine`` for the given URL.

    For in-memory SQLite (``sqlite+aiosqlite:///:memory:``) we use a
    ``StaticPool`` so the same connection — and therefore the same in-memory
    database — is reused across calls. With the default pool, each connection
    would create its own fresh ``:memory:`` and tests would observe an empty
    database between requests.
    """
    is_sqlite = database_url.startswith("sqlite")
    is_memory = is_sqlite and ":memory:" in database_url

    engine_kwargs: dict[str, Any] = {"echo": echo, "future": True}
    if is_memory:
        # StaticPool reuses a single connection — required so the same
        # ``:memory:`` database is seen across calls.
        engine_kwargs["poolclass"] = StaticPool
        engine_kwargs["connect_args"] = {"check_same_thread": False}

    engine = create_async_engine(database_url, **engine_kwargs)
    if is_sqlite:
        _install_sqlite_pragmas(engine)
    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to the engine.

    ``expire_on_commit=False`` keeps loaded attributes accessible after commit,
    which matches the way our repositories return detached domain entities.
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Attach a ``connect`` listener that flips the SQLite-specific PRAGMAs."""

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: DBAPIConnection, _: ConnectionPoolEntry) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
        finally:
            cursor.close()
