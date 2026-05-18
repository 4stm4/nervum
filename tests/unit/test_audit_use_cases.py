"""Unit-тесты ``RecordAudit`` + ``ListAuditEvents``."""

from __future__ import annotations

from datetime import timedelta

import pytest

from sdn_controller.adapters.memory import InMemoryAuditEventRepository
from sdn_controller.core.use_cases.audit import (
    ListAuditEvents,
    ListAuditEventsCommand,
    RecordAudit,
    RecordAuditCommand,
)
from tests.conftest import CountingIdFactory, FrozenClock


@pytest.fixture
def repo() -> InMemoryAuditEventRepository:
    return InMemoryAuditEventRepository()


@pytest.fixture
def record(
    repo: InMemoryAuditEventRepository, clock: FrozenClock, ids: CountingIdFactory
) -> RecordAudit:
    return RecordAudit(audit_events=repo, clock=clock, ids=ids)


@pytest.fixture
def list_uc(repo: InMemoryAuditEventRepository) -> ListAuditEvents:
    return ListAuditEvents(audit_events=repo)


async def test_record_persists_event_with_clock_and_id(
    record: RecordAudit,
    clock: FrozenClock,
) -> None:
    ev = await record.execute(
        RecordAuditCommand(action="network.create", resource_type="network", actor="ops"),
    )
    assert ev.action == "network.create"
    assert ev.at == clock.current
    assert ev.id.startswith("audit_")


async def test_list_returns_in_reverse_chrono_order(
    record: RecordAudit,
    list_uc: ListAuditEvents,
    clock: FrozenClock,
) -> None:
    await record.execute(RecordAuditCommand(action="network.create", resource_type="network"))
    clock.advance(1)
    await record.execute(RecordAuditCommand(action="network.update", resource_type="network"))

    items = await list_uc.execute(ListAuditEventsCommand())
    assert [it.action for it in items] == ["network.update", "network.create"]


async def test_list_filters_by_actor_action_resource(
    record: RecordAudit,
    list_uc: ListAuditEvents,
    clock: FrozenClock,
) -> None:
    await record.execute(
        RecordAuditCommand(
            action="network.create",
            resource_type="network",
            actor="ops",
            resource_id="net_1",
        ),
    )
    clock.advance(1)
    await record.execute(
        RecordAuditCommand(
            action="network.update",
            resource_type="network",
            actor="ci",
            resource_id="net_2",
        ),
    )

    by_actor = await list_uc.execute(ListAuditEventsCommand(actor="ops"))
    assert [it.action for it in by_actor] == ["network.create"]

    by_action = await list_uc.execute(ListAuditEventsCommand(action="network.update"))
    assert [it.resource_id for it in by_action] == ["net_2"]


async def test_list_filters_by_since(
    record: RecordAudit,
    list_uc: ListAuditEvents,
    clock: FrozenClock,
) -> None:
    await record.execute(RecordAuditCommand(action="network.create", resource_type="network"))
    cutoff = clock.advance(60)
    await record.execute(RecordAuditCommand(action="network.update", resource_type="network"))

    items = await list_uc.execute(ListAuditEventsCommand(since=cutoff - timedelta(seconds=1)))
    assert [it.action for it in items] == ["network.update"]


async def test_list_clamps_limit(
    record: RecordAudit,
    list_uc: ListAuditEvents,
    clock: FrozenClock,
) -> None:
    for i in range(5):
        await record.execute(
            RecordAuditCommand(
                action="network.create",
                resource_type="network",
                resource_id=f"net_{i}",
            ),
        )
        clock.advance(1)

    items = await list_uc.execute(ListAuditEventsCommand(limit=2))
    assert len(items) == 2
