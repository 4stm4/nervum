"""Shared fixtures: deterministic clock and id factory, ready-built container."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.memory import (
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryOperationRepository,
)
from sdn_controller.app.config import Settings
from sdn_controller.app.container import Container
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.use_cases.networks import CreateNetwork, GetNetwork, ListNetworks
from sdn_controller.core.use_cases.nodes import GetNode, ListNodes
from sdn_controller.core.use_cases.operations import GetOperation, ListOperations
from sdn_controller.core.value_objects.ids import (
    IdFactory,
    NetworkId,
    NodeId,
    OperationId,
    SubnetId,
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


_INITIAL_COUNTERS: dict[str, int] = {"node": 0, "net": 0, "sub": 0, "op": 0}


@dataclass(slots=True)
class CountingIdFactory:
    """Predictable ids: ``node_1``, ``net_1``, ``sub_1``, ``op_1`` ..."""

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
def container(clock: FrozenClock, ids: CountingIdFactory) -> Container:
    """Container built from in-memory adapters and deterministic services."""
    settings = Settings(persistence="memory", log_level="WARNING", log_format="console")
    nodes_repo = InMemoryNodeRepository()
    networks_repo = InMemoryNetworkRepository()
    operations_repo = InMemoryOperationRepository()

    clock_port: Clock = clock
    id_port: IdFactory = ids

    return Container(
        settings=settings,
        clock=clock_port,
        ids=id_port,
        nodes_repo=nodes_repo,
        networks_repo=networks_repo,
        operations_repo=operations_repo,
        create_network=CreateNetwork(
            networks=networks_repo,
            operations=operations_repo,
            clock=clock_port,
            ids=id_port,
        ),
        list_networks=ListNetworks(networks=networks_repo),
        get_network=GetNetwork(networks=networks_repo),
        list_nodes=ListNodes(nodes=nodes_repo),
        get_node=GetNode(nodes=nodes_repo),
        list_operations=ListOperations(operations=operations_repo),
        get_operation=GetOperation(operations=operations_repo),
    )


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    app = create_app(container)
    with TestClient(app) as tc:
        yield tc
