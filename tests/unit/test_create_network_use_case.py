"""CreateNetwork use case: end-to-end through in-memory adapters."""

from __future__ import annotations

import pytest

from sdn_controller.adapters.memory import (
    InMemoryNetworkRepository,
    InMemoryOperationRepository,
)
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.use_cases.networks import (
    CreateNetwork,
    CreateNetworkCommand,
    SubnetSpec,
)
from sdn_controller.core.value_objects.enums import NetworkType, OperationStatus
from sdn_controller.core.value_objects.errors import ConflictError, ValidationError
from tests.conftest import CountingIdFactory, FrozenClock


@pytest.fixture
def use_case(
    clock: FrozenClock, ids: CountingIdFactory, events: EventPublisher
) -> CreateNetwork:
    return CreateNetwork(
        networks=InMemoryNetworkRepository(),
        operations=InMemoryOperationRepository(),
        clock=clock,
        ids=ids,
        events=events,
    )


async def test_creates_network_with_operation_in_succeeded_state(
    clock: FrozenClock,
    ids: CountingIdFactory,
    events: EventPublisher,
) -> None:
    networks = InMemoryNetworkRepository()
    operations = InMemoryOperationRepository()
    create = CreateNetwork(
        networks=networks, operations=operations, clock=clock, ids=ids, events=events
    )

    result = await create.execute(
        CreateNetworkCommand(
            name="tenant-a",
            type=NetworkType.VXLAN,
            vni=10100,
            subnet=SubnetSpec(cidr="10.100.0.0/24", gateway="10.100.0.1"),
            created_by="alice",
        )
    )

    # Network was persisted.
    persisted = await networks.get_by_name("tenant-a")
    assert persisted is not None
    assert persisted.id == "net_1"
    assert persisted.type is NetworkType.VXLAN
    assert persisted.subnet is not None
    assert persisted.subnet.cidr == "10.100.0.0/24"

    # Operation walked the full state machine.
    op = await operations.get(result.operation.id)
    assert op is not None
    assert op.status is OperationStatus.SUCCEEDED
    assert op.created_by == "alice"
    statuses = [e.status for e in op.events]
    assert statuses == [
        OperationStatus.ACCEPTED,
        OperationStatus.PLANNING,
        OperationStatus.RUNNING,
        OperationStatus.VERIFYING,
        OperationStatus.SUCCEEDED,
    ]


async def test_duplicate_name_raises_conflict(
    clock: FrozenClock,
    ids: CountingIdFactory,
    events: EventPublisher,
) -> None:
    networks = InMemoryNetworkRepository()
    operations = InMemoryOperationRepository()
    create = CreateNetwork(
        networks=networks, operations=operations, clock=clock, ids=ids, events=events
    )

    cmd = CreateNetworkCommand(name="tenant-a", type=NetworkType.VLAN, vlan_id=10)
    await create.execute(cmd)

    with pytest.raises(ConflictError):
        await create.execute(cmd)


async def test_invalid_intent_does_not_create_operation(
    clock: FrozenClock,
    ids: CountingIdFactory,
    events: EventPublisher,
) -> None:
    networks = InMemoryNetworkRepository()
    operations = InMemoryOperationRepository()
    create = CreateNetwork(
        networks=networks, operations=operations, clock=clock, ids=ids, events=events
    )

    with pytest.raises(ValidationError):
        await create.execute(CreateNetworkCommand(name="bad", type=NetworkType.VXLAN, vni=None))

    assert await networks.list() == []
    assert await operations.list() == []
