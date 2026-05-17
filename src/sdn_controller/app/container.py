"""Dependency container.

The container builds repositories, services and use cases once at startup and
exposes them to HTTP handlers. We deliberately avoid a heavy DI framework —
constructor injection with a hand-written wiring layer is enough for now and
makes the dependency graph trivially auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from sdn_controller.adapters.memory import (
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryOperationRepository,
)
from sdn_controller.adapters.sql import (
    SqlNetworkRepository,
    SqlNodeRepository,
    SqlOperationRepository,
    build_engine,
    build_sessionmaker,
)
from sdn_controller.app.config import Settings
from sdn_controller.core.services.clock import Clock, SystemClock
from sdn_controller.core.use_cases.networks import CreateNetwork, GetNetwork, ListNetworks
from sdn_controller.core.use_cases.nodes import GetNode, ListNodes
from sdn_controller.core.use_cases.operations import GetOperation, ListOperations
from sdn_controller.core.value_objects.ids import IdFactory, UuidIdFactory
from sdn_controller.ports.persistence import (
    NetworkRepository,
    NodeRepository,
    OperationRepository,
)


@dataclass(slots=True)
class Container:
    """Resolved dependency graph for the running application."""

    settings: Settings
    clock: Clock
    ids: IdFactory

    nodes_repo: NodeRepository
    networks_repo: NetworkRepository
    operations_repo: OperationRepository

    create_network: CreateNetwork
    list_networks: ListNetworks
    get_network: GetNetwork
    list_nodes: ListNodes
    get_node: GetNode
    list_operations: ListOperations
    get_operation: GetOperation

    # Owned resources that need cleanup on shutdown (e.g. AsyncEngine).
    _shutdown_hooks: list[AsyncEngine] = field(default_factory=list)

    async def shutdown(self) -> None:
        for engine in self._shutdown_hooks:
            await engine.dispose()
        self._shutdown_hooks.clear()


def build_container(settings: Settings) -> Container:
    """Wire concrete adapters and use cases for the given settings."""

    clock: Clock = SystemClock()
    ids: IdFactory = UuidIdFactory()

    nodes_repo, networks_repo, operations_repo, shutdown_hooks = _build_repositories(settings)

    return Container(
        settings=settings,
        clock=clock,
        ids=ids,
        nodes_repo=nodes_repo,
        networks_repo=networks_repo,
        operations_repo=operations_repo,
        create_network=CreateNetwork(
            networks=networks_repo,
            operations=operations_repo,
            clock=clock,
            ids=ids,
        ),
        list_networks=ListNetworks(networks=networks_repo),
        get_network=GetNetwork(networks=networks_repo),
        list_nodes=ListNodes(nodes=nodes_repo),
        get_node=GetNode(nodes=nodes_repo),
        list_operations=ListOperations(operations=operations_repo),
        get_operation=GetOperation(operations=operations_repo),
        _shutdown_hooks=shutdown_hooks,
    )


def _build_repositories(
    settings: Settings,
) -> tuple[NodeRepository, NetworkRepository, OperationRepository, list[AsyncEngine]]:
    """Pick the persistence adapter based on settings.

    Returns the three repositories plus the list of resources the container
    must close on shutdown (currently: the SQLAlchemy engine, if any).
    """
    if settings.persistence == "memory":
        return (
            InMemoryNodeRepository(),
            InMemoryNetworkRepository(),
            InMemoryOperationRepository(),
            [],
        )

    if settings.persistence in {"sqlite", "postgres"}:
        engine = build_engine(settings.database_url, echo=settings.database_echo)
        sessionmaker = build_sessionmaker(engine)
        return (
            SqlNodeRepository(sessionmaker),
            SqlNetworkRepository(sessionmaker),
            SqlOperationRepository(sessionmaker),
            [engine],
        )

    raise NotImplementedError(f"unsupported persistence backend: {settings.persistence!r}")
