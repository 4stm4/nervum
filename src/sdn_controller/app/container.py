"""Dependency container.

The container builds repositories, services and use cases once at startup and
exposes them to HTTP handlers. We deliberately avoid a heavy DI framework —
constructor injection with a hand-written wiring layer is enough for now and
makes the dependency graph trivially auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdn_controller.adapters.memory import (
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryOperationRepository,
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


def build_container(settings: Settings) -> Container:
    """Wire concrete adapters and use cases for the given settings."""

    clock: Clock = SystemClock()
    ids: IdFactory = UuidIdFactory()

    # Storage. PostgreSQL adapter lands in SDN-002 and will be selected here
    # when ``settings.persistence == "postgres"``.
    if settings.persistence != "memory":
        raise NotImplementedError(
            f"persistence backend {settings.persistence!r} not implemented yet"
        )

    nodes_repo = InMemoryNodeRepository()
    networks_repo = InMemoryNetworkRepository()
    operations_repo = InMemoryOperationRepository()

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
    )
