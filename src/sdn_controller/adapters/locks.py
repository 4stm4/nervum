"""Реализации ``LockStore`` (M13 — SDN-037).

* ``InMemoryLockStore`` — dict + ``anyio.Lock``. Подходит для тестов и
  для single-replica развёртывания (``persistence=memory``).
* ``SqlLockStore`` — таблица ``operation_locks`` (``key`` PK,
  ``owner``, ``expires_at``). Работает одинаково на SQLite и Postgres,
  не требует session-bound advisory lock'а. Меньше эффективно, чем
  ``pg_try_advisory_lock`` — но и не привязывает себя к коннекшену,
  это полезно для нашей session-per-request архитектуры.

Все операции atomic: на SQL — INSERT с UNIQUE-конфликтом и DELETE с
условием owner+expires_at; на in-memory — под общим ``anyio.Lock``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import anyio
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sdn_controller.adapters.sql.models import OperationLockRow
from sdn_controller.core.services.clock import Clock


class InMemoryLockStore:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._held: dict[str, tuple[str, datetime]] = {}
        self._mutex = anyio.Lock()

    async def try_lock(self, key: str, *, owner: str, ttl_seconds: int) -> bool:
        now = self._clock.now()
        async with self._mutex:
            existing = self._held.get(key)
            if existing is not None and existing[1] > now:
                # ещё не истёк — owner может re-acquire, чужой — нет
                return existing[0] == owner
            self._held[key] = (owner, now + timedelta(seconds=ttl_seconds))
            return True

    async def release(self, key: str, *, owner: str) -> None:
        async with self._mutex:
            existing = self._held.get(key)
            if existing is None:
                return
            if existing[0] != owner:
                return  # чужой лок не трогаем
            self._held.pop(key, None)

    async def current_owner(self, key: str) -> str | None:
        now = self._clock.now()
        async with self._mutex:
            existing = self._held.get(key)
            if existing is None or existing[1] <= now:
                return None
            return existing[0]


class SqlLockStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def try_lock(self, key: str, *, owner: str, ttl_seconds: int) -> bool:
        now = self._clock.now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        async with self._session_factory() as session:
            # Сначала чистим просроченный лок по этому ключу — иначе
            # INSERT упадёт на UNIQUE без шанса перехватить.
            await session.execute(
                delete(OperationLockRow).where(
                    OperationLockRow.key == key,
                    OperationLockRow.expires_at <= now,
                )
            )
            try:
                await session.execute(
                    insert(OperationLockRow).values(
                        key=key,
                        owner=owner,
                        expires_at=expires_at,
                    )
                )
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                # Кто-то живой держит лок; для same owner допускаем
                # «уже наше», чтобы повторный вызов не падал.
                row = await session.get(OperationLockRow, key)
                return row is not None and row.owner == owner

    async def release(self, key: str, *, owner: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(OperationLockRow).where(
                    OperationLockRow.key == key,
                    OperationLockRow.owner == owner,
                )
            )
            await session.commit()

    async def current_owner(self, key: str) -> str | None:
        now = self._clock.now()
        async with self._session_factory() as session:
            row = (
                await session.scalars(
                    select(OperationLockRow).where(
                        OperationLockRow.key == key,
                        OperationLockRow.expires_at > now,
                    )
                )
            ).one_or_none()
            return row.owner if row is not None else None


__all__ = ["InMemoryLockStore", "SqlLockStore"]
