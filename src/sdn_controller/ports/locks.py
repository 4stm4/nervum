"""Распределённый порт ``LockStore`` (M13 — SDN-037).

Зачем: ``ApplyNetwork`` на одной и той же сети не должен крутиться
параллельно из двух реплик — это сразу даёт race на observed state и
двойной push в агент. Решение — взятие именованного лока на
``network:<id>`` до старта планировщика.

Контракт:

* ``try_lock(key, ttl) → bool`` — atomic. Если вернул ``True``,
  значит держим до явного ``release`` или истечения ``ttl``;
  ``False`` — кто-то другой держит, попробуйте позже.
* ``release(key)`` — снять лок. **Идемпотентно**: повторный release
  по не-нашему ключу или просроченному локу — no-op.
* ``current_owner(key) → str | None`` — для информативного 409:
  «кто сейчас apply-ит эту сеть».

TTL нужен, чтобы зависший процесс не залочил сеть навсегда.
Безопасное значение — больше типичного apply времени, но меньше
терпения оператора (5 минут по умолчанию).

Будущая etcd/redis-реализация — отдельный адаптер; контракт тот же.
"""

from __future__ import annotations

from typing import Protocol


class LockStore(Protocol):
    async def try_lock(self, key: str, *, owner: str, ttl_seconds: int) -> bool: ...
    async def release(self, key: str, *, owner: str) -> None: ...
    async def current_owner(self, key: str) -> str | None: ...


__all__ = ["LockStore"]
