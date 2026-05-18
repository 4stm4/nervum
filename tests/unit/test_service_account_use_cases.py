"""Unit-тесты use case'ов M9: create/issue/revoke/authenticate."""

from __future__ import annotations

import pytest

from sdn_controller.adapters.memory import (
    InMemoryServiceAccountRepository,
    InMemoryServiceTokenRepository,
)
from sdn_controller.core.use_cases.service_accounts import (
    AuthenticatePrincipal,
    CreateServiceAccount,
    CreateServiceAccountCommand,
    DisableServiceAccount,
    IssueServiceToken,
    IssueServiceTokenCommand,
    ListServiceTokens,
    RevokeServiceToken,
)
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import ServiceAccountId, ServiceTokenId
from sdn_controller.core.value_objects.security import Role
from tests.conftest import CountingIdFactory, FrozenClock, SequentialTokenFactory


@pytest.fixture
def repos() -> tuple[InMemoryServiceAccountRepository, InMemoryServiceTokenRepository]:
    return InMemoryServiceAccountRepository(), InMemoryServiceTokenRepository()


@pytest.fixture
def create(
    repos: tuple[InMemoryServiceAccountRepository, InMemoryServiceTokenRepository],
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> CreateServiceAccount:
    accounts, _ = repos
    return CreateServiceAccount(accounts=accounts, clock=clock, ids=ids)


@pytest.fixture
def issue(
    repos: tuple[InMemoryServiceAccountRepository, InMemoryServiceTokenRepository],
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
) -> IssueServiceToken:
    accounts, tokens = repos
    return IssueServiceToken(
        accounts=accounts,
        tokens=tokens,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
    )


@pytest.fixture
def authenticate(
    repos: tuple[InMemoryServiceAccountRepository, InMemoryServiceTokenRepository],
    clock: FrozenClock,
) -> AuthenticatePrincipal:
    accounts, tokens = repos
    return AuthenticatePrincipal(accounts=accounts, tokens=tokens, clock=clock)


# ---------------------------------------------------------------------------
# CreateServiceAccount
# ---------------------------------------------------------------------------


async def test_create_account_persists_and_returns_active(create: CreateServiceAccount) -> None:
    account = await create.execute(
        CreateServiceAccountCommand(name="prod-ci", role=Role.AUTOMATION),
    )
    assert account.is_active
    assert account.role is Role.AUTOMATION
    assert account.name == "prod-ci"


async def test_create_duplicate_name_raises_conflict(create: CreateServiceAccount) -> None:
    await create.execute(CreateServiceAccountCommand(name="ci", role=Role.AUTOMATION))
    with pytest.raises(ConflictError):
        await create.execute(CreateServiceAccountCommand(name="ci", role=Role.AUTOMATION))


async def test_create_with_blank_name_raises_validation(create: CreateServiceAccount) -> None:
    with pytest.raises(ValidationError):
        await create.execute(CreateServiceAccountCommand(name="  ", role=Role.VIEWER))


# ---------------------------------------------------------------------------
# IssueServiceToken
# ---------------------------------------------------------------------------


async def test_issue_token_returns_plaintext_once(
    create: CreateServiceAccount,
    issue: IssueServiceToken,
) -> None:
    account = await create.execute(CreateServiceAccountCommand(name="ci", role=Role.AUTOMATION))

    issued = await issue.execute(IssueServiceTokenCommand(account_id=account.id))

    assert issued.plaintext.startswith("test-svc-token-")
    assert issued.token.service_account_id == account.id
    assert issued.token.expires_at is None


async def test_issue_token_with_ttl_sets_expiry(
    create: CreateServiceAccount,
    issue: IssueServiceToken,
    clock: FrozenClock,
) -> None:
    account = await create.execute(CreateServiceAccountCommand(name="ci", role=Role.AUTOMATION))
    issued = await issue.execute(
        IssueServiceTokenCommand(account_id=account.id, ttl_seconds=3600),
    )
    assert issued.token.expires_at is not None
    assert (issued.token.expires_at - clock.current).total_seconds() == 3600


async def test_issue_for_unknown_account_raises_not_found(issue: IssueServiceToken) -> None:
    with pytest.raises(NotFoundError):
        await issue.execute(IssueServiceTokenCommand(account_id=ServiceAccountId("missing")))


async def test_cannot_issue_for_disabled_account(
    create: CreateServiceAccount,
    issue: IssueServiceToken,
    repos: tuple[InMemoryServiceAccountRepository, InMemoryServiceTokenRepository],
    clock: FrozenClock,
) -> None:
    accounts, _ = repos
    account = await create.execute(CreateServiceAccountCommand(name="ci", role=Role.AUTOMATION))
    await DisableServiceAccount(accounts=accounts, clock=clock).execute(account.id)

    with pytest.raises(ConflictError):
        await issue.execute(IssueServiceTokenCommand(account_id=account.id))


# ---------------------------------------------------------------------------
# AuthenticatePrincipal — горячий путь
# ---------------------------------------------------------------------------


async def test_authenticate_valid_token_returns_principal(
    create: CreateServiceAccount,
    issue: IssueServiceToken,
    authenticate: AuthenticatePrincipal,
) -> None:
    account = await create.execute(CreateServiceAccountCommand(name="ci", role=Role.AUTOMATION))
    issued = await issue.execute(IssueServiceTokenCommand(account_id=account.id))

    principal = await authenticate.execute(issued.plaintext)
    assert principal.service_account_id == account.id
    assert principal.role is Role.AUTOMATION
    assert principal.name == "ci"


async def test_authenticate_revoked_token_rejected(
    create: CreateServiceAccount,
    issue: IssueServiceToken,
    authenticate: AuthenticatePrincipal,
    repos: tuple[InMemoryServiceAccountRepository, InMemoryServiceTokenRepository],
    clock: FrozenClock,
) -> None:
    account = await create.execute(CreateServiceAccountCommand(name="ci", role=Role.AUTOMATION))
    issued = await issue.execute(IssueServiceTokenCommand(account_id=account.id))
    _, tokens = repos
    await RevokeServiceToken(tokens=tokens, clock=clock).execute(issued.token.id)

    with pytest.raises(UnauthorizedError):
        await authenticate.execute(issued.plaintext)


async def test_authenticate_expired_token_rejected(
    create: CreateServiceAccount,
    issue: IssueServiceToken,
    authenticate: AuthenticatePrincipal,
    clock: FrozenClock,
) -> None:
    account = await create.execute(CreateServiceAccountCommand(name="ci", role=Role.AUTOMATION))
    issued = await issue.execute(
        IssueServiceTokenCommand(account_id=account.id, ttl_seconds=60),
    )
    clock.advance(120)

    with pytest.raises(UnauthorizedError):
        await authenticate.execute(issued.plaintext)


async def test_authenticate_disabled_account_rejected(
    create: CreateServiceAccount,
    issue: IssueServiceToken,
    authenticate: AuthenticatePrincipal,
    repos: tuple[InMemoryServiceAccountRepository, InMemoryServiceTokenRepository],
    clock: FrozenClock,
) -> None:
    account = await create.execute(CreateServiceAccountCommand(name="ci", role=Role.AUTOMATION))
    issued = await issue.execute(IssueServiceTokenCommand(account_id=account.id))
    accounts, _ = repos
    await DisableServiceAccount(accounts=accounts, clock=clock).execute(account.id)

    with pytest.raises(UnauthorizedError):
        await authenticate.execute(issued.plaintext)


async def test_authenticate_unknown_token_rejected(authenticate: AuthenticatePrincipal) -> None:
    with pytest.raises(UnauthorizedError):
        await authenticate.execute("nope")


async def test_authenticate_blank_plaintext_rejected(authenticate: AuthenticatePrincipal) -> None:
    with pytest.raises(UnauthorizedError):
        await authenticate.execute("")


# ---------------------------------------------------------------------------
# RevokeServiceToken / ListServiceTokens
# ---------------------------------------------------------------------------


async def test_revoke_is_idempotent(
    create: CreateServiceAccount,
    issue: IssueServiceToken,
    repos: tuple[InMemoryServiceAccountRepository, InMemoryServiceTokenRepository],
    clock: FrozenClock,
) -> None:
    account = await create.execute(CreateServiceAccountCommand(name="ci", role=Role.AUTOMATION))
    issued = await issue.execute(IssueServiceTokenCommand(account_id=account.id))
    _, tokens = repos

    revoke = RevokeServiceToken(tokens=tokens, clock=clock)
    first = await revoke.execute(issued.token.id)
    second = await revoke.execute(issued.token.id)
    assert first.revoked_at == second.revoked_at


async def test_revoke_unknown_token_raises_not_found(
    repos: tuple[InMemoryServiceAccountRepository, InMemoryServiceTokenRepository],
    clock: FrozenClock,
) -> None:
    _, tokens = repos
    with pytest.raises(NotFoundError):
        await RevokeServiceToken(tokens=tokens, clock=clock).execute(
            ServiceTokenId("missing"),
        )


async def test_list_tokens_for_unknown_account_raises_not_found(
    repos: tuple[InMemoryServiceAccountRepository, InMemoryServiceTokenRepository],
) -> None:
    accounts, tokens = repos
    with pytest.raises(NotFoundError):
        await ListServiceTokens(accounts=accounts, tokens=tokens).execute(
            ServiceAccountId("missing"),
        )
