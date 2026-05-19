"""Shared fixtures: deterministic clock, id factory, token factory, container."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.memory import InMemoryOutboxRepository
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.app.config import Settings
from sdn_controller.app.container import Container, build_container
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.value_objects.ids import (
    AuditEventId,
    EnrollmentTokenId,
    IpAllocationId,
    NetworkId,
    NodeId,
    NodeSnapshotId,
    OperationId,
    OutboxEventId,
    ServiceAccountId,
    ServiceTokenId,
    SubnetId,
    WebhookSubscriptionId,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FrozenClock:
    """A clock the tests can advance deterministically."""

    current: datetime = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float = 1.0) -> datetime:
        self.current = self.current + timedelta(seconds=seconds)
        return self.current


_INITIAL_COUNTERS: dict[str, int] = {
    "node": 0,
    "net": 0,
    "sub": 0,
    "op": 0,
    "enroll": 0,
    "ipa": 0,
    "sa": 0,
    "tok": 0,
    "audit": 0,
    "snap": 0,
    "outbox": 0,
    "whsub": 0,
}


@dataclass(slots=True)
class CountingIdFactory:
    """Predictable ids: ``node_1``, ``net_1``, ``sub_1``, ``op_1``, ``enroll_1`` ..."""

    _counters: dict[str, int] = field(default_factory=lambda: dict(_INITIAL_COUNTERS))

    def _next(self, prefix: str) -> str:
        self._counters[prefix] += 1
        return f"{prefix}_{self._counters[prefix]}"

    def node(self) -> NodeId:
        return NodeId(self._next("node"))

    def network(self) -> NetworkId:
        return NetworkId(self._next("net"))

    def subnet(self) -> SubnetId:
        return SubnetId(self._next("sub"))

    def operation(self) -> OperationId:
        return OperationId(self._next("op"))

    def enrollment_token(self) -> EnrollmentTokenId:
        return EnrollmentTokenId(self._next("enroll"))

    def ip_allocation(self) -> IpAllocationId:
        return IpAllocationId(self._next("ipa"))

    def service_account(self) -> ServiceAccountId:
        return ServiceAccountId(self._next("sa"))

    def service_token(self) -> ServiceTokenId:
        return ServiceTokenId(self._next("tok"))

    def audit_event(self) -> AuditEventId:
        return AuditEventId(self._next("audit"))

    def node_snapshot(self) -> NodeSnapshotId:
        return NodeSnapshotId(self._next("snap"))

    def outbox_event(self) -> OutboxEventId:
        return OutboxEventId(self._next("outbox"))

    def webhook_subscription(self) -> WebhookSubscriptionId:
        return WebhookSubscriptionId(self._next("whsub"))


@dataclass(slots=True)
class SequentialTokenFactory:
    """Predictable enrolment + service token plaintexts.

    Production code uses ``SecretsTokenFactory``; tests substitute this so
    assertions can reference the exact plaintext.
    """

    _enroll_counter: int = 0
    _service_counter: int = 0

    def enrollment_token_plaintext(self) -> str:
        self._enroll_counter += 1
        return f"test-token-{self._enroll_counter}"

    def service_token_plaintext(self) -> str:
        self._service_counter += 1
        return f"test-svc-token-{self._service_counter}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def ids() -> CountingIdFactory:
    return CountingIdFactory()


@pytest.fixture
def token_factory() -> SequentialTokenFactory:
    return SequentialTokenFactory()


@pytest.fixture
def fake_agent(clock: FrozenClock) -> FakeAgent:
    return FakeAgent(clock=clock)


@pytest.fixture
def outbox() -> InMemoryOutboxRepository:
    """Shared outbox-stub для unit-тестов, которые конструируют use cases напрямую."""
    return InMemoryOutboxRepository()


@pytest.fixture
def events(
    outbox: InMemoryOutboxRepository,
    clock: FrozenClock,
    ids: CountingIdFactory,
) -> EventPublisher:
    return EventPublisher(outbox=outbox, clock=clock, ids=ids)


@pytest.fixture
def container(
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    fake_agent: FakeAgent,
) -> Container:
    """Container built from in-memory adapters and deterministic services.

    ``auth_enabled=False`` — стандарт для всех существующих тестов
    (M2–M8). M9-специфичные тесты сами поднимают контейнер с
    ``auth_enabled=True`` через локальные фикстуры.
    """
    settings = Settings(
        persistence="memory",
        log_level="WARNING",
        log_format="console",
        auth_enabled=False,
    )
    return build_container(
        settings,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        agent=fake_agent,
    )


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    app = create_app(container)
    with TestClient(app) as tc:
        yield tc
