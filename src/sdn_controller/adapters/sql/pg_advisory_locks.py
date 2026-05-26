"""PgAdvisoryLockStore — распределённые блокировки через PostgreSQL advisory locks (N5-04).

Заменяет ``SqlLockStore`` (INSERT-based) для Postgres-деплоев:
использует ``pg_try_advisory_lock`` / ``pg_advisory_unlock`` вместо таблицы
``operation_locks``. Преимущества:
  * Автоматически освобождается при разрыве соединения.
  * Не требует индексов и VACUUM.
  * Атомарность гарантирована на уровне PG-ядра.

Ограничения:
  * Session-level лок привязан к pg-соединению. В async пуле с
    множеством коннектов нужно использовать ``acquire_raw_connection``
    и держать тот же коннект весь жизненный цикл лока.
  * ``current_owner()`` возвращает None — advisory locks не имеют
    встроенного хранения метаданных владельца.

Ключ (строка) → bigint через FNV-1a, что даёт равномерное распределение
и детерминизм между запусками.
"""

from __future__ import annotations

import struct
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_log = structlog.get_logger(__name__)

# Константы FNV-1a (64-bit)
_FNV1A_PRIME: int = 0x00000100000001B3
_FNV1A_OFFSET: int = 0xCBF29CE484222325
_U64_MASK: int = 0xFFFFFFFFFFFFFFFF


def _key_to_bigint(key: str) -> int:
    """FNV-1a 64-bit хэш строкового ключа → signed int64.

    PostgreSQL ``pg_try_advisory_lock`` принимает bigint (signed int64),
    поэтому приводим unsigned u64 к signed через struct reinterpret.
    """
    h = _FNV1A_OFFSET
    for byte in key.encode():
        h ^= byte
        h = (h * _FNV1A_PRIME) & _U64_MASK
    # Reinterpret u64 → signed i64
    packed = struct.pack(">Q", h)
    return struct.unpack(">q", packed)[0]


class PgAdvisoryLockStore:
    """Session-level advisory locks через pg_try_advisory_lock (N5-04).

    Важно: каждый lock/unlock должен использовать одно и то же
    pg-соединение. ``_conn_map`` хранит закреплённое соединение
    на ключ. При освобождении соединение возвращается в пул.

    Для production рекомендуется использовать ``acquire_raw_connection``
    через SQLAlchemy 2.x:
        conn = await engine.raw_connection()
        try:
            pg_conn = conn.connection  # asyncpg Connection
            await pg_conn.fetchval("SELECT pg_try_advisory_lock($1)", key)
        finally:
            await conn.aclose()
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        # key → (AsyncConnection, owner)
        self._conn_map: dict[str, tuple[AsyncConnection, str]] = {}

    async def try_lock(self, key: str, *, owner: str, ttl_seconds: int) -> bool:
        """Попытаться взять advisory lock.

        ``ttl_seconds`` игнорируется: advisory lock существует, пока
        соединение живо или пока явно не вызван ``release``.
        """
        key_int = _key_to_bigint(key)

        # Если уже держим лок этим owner — идемпотентный re-acquire
        existing = self._conn_map.get(key)
        if existing is not None:
            _, existing_owner = existing
            return existing_owner == owner

        # Получаем сырое соединение (не transaction-bound)
        conn: AsyncConnection = await self._engine.connect()
        try:
            result = await conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": key_int},
            )
            acquired: bool = bool(result.scalar())
            if acquired:
                self._conn_map[key] = (conn, owner)
                _log.debug("pg_advisory_lock_acquired", key=key, owner=owner)
                return True
            else:
                await conn.close()
                _log.debug("pg_advisory_lock_busy", key=key)
                return False
        except Exception:
            await conn.close()
            raise

    async def release(self, key: str, *, owner: str) -> None:
        """Освободить advisory lock. Идемпотентно."""
        existing = self._conn_map.get(key)
        if existing is None:
            return
        conn, existing_owner = existing
        if existing_owner != owner:
            _log.warning("pg_advisory_unlock_wrong_owner", key=key, owner=owner)
            return
        key_int = _key_to_bigint(key)
        try:
            await conn.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": key_int},
            )
            _log.debug("pg_advisory_lock_released", key=key, owner=owner)
        finally:
            del self._conn_map[key]
            await conn.close()

    async def current_owner(self, key: str) -> str | None:
        """Вернуть владельца лока.

        Advisory locks не хранят метаданные владельца — возвращаем
        данные из локального кэша (актуально только в рамках процесса).
        """
        existing = self._conn_map.get(key)
        if existing is None:
            return None
        return existing[1]

    async def is_locked(self, key: str) -> bool:
        """Проверить, заблокирован ли ключ (любым процессом).

        Использует ``pg_locks`` системный вид.
        """
        key_int = _key_to_bigint(key)
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT EXISTS("
                    "  SELECT 1 FROM pg_locks "
                    "  WHERE locktype='advisory' "
                    "  AND objid=:key AND granted"
                    ")"
                ),
                {"key": key_int & 0xFFFFFFFF},
            )
            return bool(result.scalar())


__all__ = ["PgAdvisoryLockStore", "_key_to_bigint"]
