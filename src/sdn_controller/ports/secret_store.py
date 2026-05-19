"""Порт хранения секретов (SDN-043).

«Секрет» здесь = plaintext, который контроллер получил один раз
(например, webhook-secret при создании подписки) и которым нужно
пользоваться позже (для подсчёта HMAC исходящих webhook'ов).

Реализации:

* ``InMemorySecretStore`` — process-local dict. Не выживает рестарт;
  для dev и для intentionally-ephemeral подписок.
* ``FernetSecretStore`` — JSON-файл, зашифрованный Fernet'ом по
  мастер-ключу из ``SDN_SECRET_STORE_KEY``. Файл создаётся с
  ``chmod 600``. Это правильный default для prod без vault'а.

Будущее расширение: Vault / AWS Secrets Manager / SOPS / k8s
secrets — отдельным адаптером, тот же интерфейс.
"""

from __future__ import annotations

from typing import Protocol


class SecretStore(Protocol):
    """Простой key/value-стор для plaintext-секретов."""

    async def remember(self, key: str, plaintext: str) -> None: ...
    async def get(self, key: str) -> str | None: ...
    async def forget(self, key: str) -> None: ...


__all__ = ["SecretStore"]
