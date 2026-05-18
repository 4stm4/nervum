"""Service accounts + tokens (SDN-028/030).

``ServiceAccount`` — это «учётка» с фиксированной ролью. У одной учётки
может быть несколько активных токенов (для ротации без даунтайма).
``ServiceToken`` хранит только SHA-256 хэш plaintext'а: оригинал
выдаётся ровно один раз в ответе на ``IssueServiceToken``.

Все принципы те же, что у enrollment-токенов (M2): plaintext не лежит
в БД, отзыв — это запись timestamp'а в строку, а не удаление (нам нужен
аудит, кто и когда отозвал что).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import ServiceAccountId, ServiceTokenId
from sdn_controller.core.value_objects.security import Role

_MAX_NAME_LENGTH = 128


def generate_service_token_plaintext() -> str:
    """64 hex-символа = 256 бит энтропии. Хватит за глаза."""
    return secrets.token_hex(32)


def hash_service_token(plaintext: str) -> str:
    """SHA-256 без соли (как у enrollment-токенов) — токены сами по себе
    высокоэнтропийные, соль не добавит безопасности."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ServiceAccount:
    """Сервисная учётка с фиксированной ролью."""

    id: ServiceAccountId
    name: str
    role: Role
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    description: str | None = None
    disabled_at: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("service account name must be non-empty")
        if len(self.name) > _MAX_NAME_LENGTH:
            raise ValidationError(f"service account name too long (max {_MAX_NAME_LENGTH})")

    @property
    def is_active(self) -> bool:
        return self.disabled_at is None

    def disable(self, *, now: datetime) -> None:
        if self.disabled_at is not None:
            return
        self.disabled_at = now
        self.updated_at = now


@dataclass(slots=True)
class ServiceToken:
    """Один токен сервисного аккаунта.

    Если ``expires_at`` ``None`` — токен не истекает (для долгоживущих
    автоматов). ``revoked_at`` отдельный — отзыв и истечение это два
    разных пути выбытия.
    """

    id: ServiceTokenId
    service_account_id: ServiceAccountId
    token_hash: str
    issued_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    issued_by: str | None = None
    label: str | None = None

    def is_valid(self, *, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        return not (self.expires_at is not None and now >= self.expires_at)

    def revoke(self, *, now: datetime) -> None:
        if self.revoked_at is None:
            self.revoked_at = now

    def touch(self, *, now: datetime) -> None:
        """Зафиксировать последнее использование."""
        self.last_used_at = now


@dataclass(frozen=True, slots=True)
class Principal:
    """То, во что превращается успешно аутентифицированный запрос.

    Содержит только то, что нужно для авторизации: id учётки, её роль,
    имя для логов. Сам токен здесь не хранится — он уже отработал.
    """

    service_account_id: ServiceAccountId
    name: str
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


__all__ = [
    "Principal",
    "ServiceAccount",
    "ServiceToken",
    "generate_service_token_plaintext",
    "hash_service_token",
]
