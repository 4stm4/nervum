"""Use cases для service accounts и токенов (M9 — SDN-028/030).

Каждый use case изолирован: создание учётки не выдаёт токен (это
отдельный шаг — оператор может создать аккаунт заранее, а токен
выпустить позже), отзыв токена не трогает аккаунт.

Аутентификация принципала вынесена в ``AuthenticatePrincipal``: это
**горячий путь** — он бьётся на каждый запрос. Поэтому он смотрит ровно
один индекс (``ServiceTokenRepository.get_by_hash``) и одну запись
аккаунта; никаких списков.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sdn_controller.core.entities import (
    Principal,
    ServiceAccount,
    ServiceToken,
    hash_service_token,
)
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import (
    IdFactory,
    ServiceAccountId,
    ServiceTokenId,
)
from sdn_controller.core.value_objects.security import Role
from sdn_controller.ports.persistence import (
    ServiceAccountRepository,
    ServiceTokenRepository,
)
from sdn_controller.ports.security import TokenFactory

# ---------------------------------------------------------------------------
# Команды / результаты
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateServiceAccountCommand:
    name: str
    role: Role
    description: str | None = None
    labels: dict[str, str] | None = None
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class IssueServiceTokenCommand:
    """Если ``ttl_seconds`` ``None`` — токен не истекает."""

    account_id: ServiceAccountId
    ttl_seconds: int | None = None
    label: str | None = None
    issued_by: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedServiceToken:
    """plaintext возвращается ровно один раз — на ответе на ``IssueServiceToken``."""

    token: ServiceToken
    plaintext: str


# ---------------------------------------------------------------------------
# Use cases — service accounts
# ---------------------------------------------------------------------------


class CreateServiceAccount:
    def __init__(
        self,
        *,
        accounts: ServiceAccountRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._accounts = accounts
        self._clock = clock
        self._ids = ids

    async def execute(self, cmd: CreateServiceAccountCommand) -> ServiceAccount:
        name = cmd.name.strip()
        if not name:
            raise ValidationError("service account name must be non-empty")
        existing = await self._accounts.get_by_name(name)
        if existing is not None:
            raise ConflictError(f"service account with name {name!r} already exists")

        now = self._clock.now()
        account = ServiceAccount(
            id=self._ids.service_account(),
            name=name,
            role=cmd.role,
            created_at=now,
            updated_at=now,
            created_by=cmd.created_by,
            description=cmd.description,
            labels=dict(cmd.labels or {}),
        )
        await self._accounts.save(account)
        return account


class ListServiceAccounts:
    def __init__(self, *, accounts: ServiceAccountRepository) -> None:
        self._accounts = accounts

    async def execute(self) -> list[ServiceAccount]:
        return await self._accounts.list()


class GetServiceAccount:
    def __init__(self, *, accounts: ServiceAccountRepository) -> None:
        self._accounts = accounts

    async def execute(self, account_id: ServiceAccountId) -> ServiceAccount:
        account = await self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(f"service account {account_id} not found")
        return account


class DisableServiceAccount:
    """Запрещает дальнейшую аутентификацию через все токены аккаунта."""

    def __init__(
        self,
        *,
        accounts: ServiceAccountRepository,
        clock: Clock,
    ) -> None:
        self._accounts = accounts
        self._clock = clock

    async def execute(self, account_id: ServiceAccountId) -> ServiceAccount:
        account = await self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(f"service account {account_id} not found")
        account.disable(now=self._clock.now())
        await self._accounts.save(account)
        return account


# ---------------------------------------------------------------------------
# Use cases — service tokens
# ---------------------------------------------------------------------------


class IssueServiceToken:
    """Выпустить новый токен для существующего аккаунта."""

    def __init__(
        self,
        *,
        accounts: ServiceAccountRepository,
        tokens: ServiceTokenRepository,
        clock: Clock,
        ids: IdFactory,
        token_factory: TokenFactory,
    ) -> None:
        self._accounts = accounts
        self._tokens = tokens
        self._clock = clock
        self._ids = ids
        self._tf = token_factory

    async def execute(self, cmd: IssueServiceTokenCommand) -> IssuedServiceToken:
        account = await self._accounts.get(cmd.account_id)
        if account is None:
            raise NotFoundError(f"service account {cmd.account_id} not found")
        if not account.is_active:
            raise ConflictError(f"service account {account.name!r} is disabled")
        if cmd.ttl_seconds is not None and cmd.ttl_seconds <= 0:
            raise ValidationError("ttl_seconds must be > 0")

        plaintext = self._tf.service_token_plaintext()
        now = self._clock.now()
        token = ServiceToken(
            id=self._ids.service_token(),
            service_account_id=cmd.account_id,
            token_hash=hash_service_token(plaintext),
            issued_at=now,
            expires_at=(now + timedelta(seconds=cmd.ttl_seconds))
            if cmd.ttl_seconds is not None
            else None,
            issued_by=cmd.issued_by,
            label=cmd.label,
        )
        await self._tokens.save(token)
        return IssuedServiceToken(token=token, plaintext=plaintext)


class RevokeServiceToken:
    """Помечает токен отозванным. Идемпотентна: повторный отзыв — no-op."""

    def __init__(
        self,
        *,
        tokens: ServiceTokenRepository,
        clock: Clock,
    ) -> None:
        self._tokens = tokens
        self._clock = clock

    async def execute(self, token_id: ServiceTokenId) -> ServiceToken:
        token = await self._tokens.get(token_id)
        if token is None:
            raise NotFoundError(f"service token {token_id} not found")
        token.revoke(now=self._clock.now())
        await self._tokens.save(token)
        return token


class ListServiceTokens:
    """Все токены конкретного аккаунта (с признаком revoked/expired).

    Возвращает токены без plaintext'а — он есть только в момент выпуска.
    """

    def __init__(
        self,
        *,
        accounts: ServiceAccountRepository,
        tokens: ServiceTokenRepository,
    ) -> None:
        self._accounts = accounts
        self._tokens = tokens

    async def execute(self, account_id: ServiceAccountId) -> list[ServiceToken]:
        # Сначала убедимся, что аккаунт реально существует — иначе вернём 404,
        # а не пустой список (так каркас REST-ответов остаётся честным).
        account = await self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(f"service account {account_id} not found")
        return await self._tokens.list_for_account(account_id)


# ---------------------------------------------------------------------------
# Authentication (горячий путь)
# ---------------------------------------------------------------------------


class AuthenticatePrincipal:
    """По plaintext'у Bearer-токена возвращает ``Principal``.

    Считает «токен невалиден» во всех неоднозначных случаях:
    нет такого хэша, отозван, истёк, аккаунт заблокирован, аккаунт удалён.
    Аутентификация всегда возвращает только ``UnauthorizedError`` — мы не
    говорим клиенту «токен есть, но аккаунт заблокирован» отдельным
    кодом: это лишняя утечка состояния системы.

    При успехе обновляет ``token.last_used_at`` — оператор сможет
    увидеть в списке токенов, какие из них действительно живые.
    """

    def __init__(
        self,
        *,
        accounts: ServiceAccountRepository,
        tokens: ServiceTokenRepository,
        clock: Clock,
    ) -> None:
        self._accounts = accounts
        self._tokens = tokens
        self._clock = clock

    async def execute(self, plaintext: str) -> Principal:
        if not plaintext:
            raise UnauthorizedError("missing bearer token")
        token = await self._tokens.get_by_hash(hash_service_token(plaintext))
        if token is None:
            raise UnauthorizedError("invalid bearer token")
        now = self._clock.now()
        if not token.is_valid(now=now):
            raise UnauthorizedError("invalid bearer token")
        account = await self._accounts.get(token.service_account_id)
        if account is None or not account.is_active:
            raise UnauthorizedError("invalid bearer token")

        # Тёплое обновление — не блокирует пользователя, если запись
        # уже изменена кем-то параллельно.
        token.touch(now=now)
        await self._tokens.save(token)

        return Principal(
            service_account_id=account.id,
            name=account.name,
            role=account.role,
        )


__all__ = [
    "AuthenticatePrincipal",
    "CreateServiceAccount",
    "CreateServiceAccountCommand",
    "DisableServiceAccount",
    "GetServiceAccount",
    "IssueServiceToken",
    "IssueServiceTokenCommand",
    "IssuedServiceToken",
    "ListServiceAccounts",
    "ListServiceTokens",
    "RevokeServiceToken",
]
