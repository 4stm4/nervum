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
    InMemoryEnrollmentTokenRepository,
    InMemoryIpAllocationRepository,
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryObservedStateRepository,
    InMemoryOperationRepository,
)
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.adapters.security import SecretsTokenFactory
from sdn_controller.adapters.sql import (
    SqlEnrollmentTokenRepository,
    SqlIpAllocationRepository,
    SqlNetworkRepository,
    SqlNodeRepository,
    SqlObservedStateRepository,
    SqlOperationRepository,
    build_engine,
    build_sessionmaker,
)
from sdn_controller.app.config import Settings
from sdn_controller.core.services.clock import Clock, SystemClock
from sdn_controller.core.services.planner import Planner
from sdn_controller.core.use_cases.enrollment import (
    EnrollAgent,
    IssueEnrollmentToken,
    RecordHeartbeat,
)
from sdn_controller.core.use_cases.ipam import (
    AllocateIp,
    GetAllocation,
    GetSubnet,
    ListAllocations,
    ListSubnets,
    ReleaseIp,
    ReserveIp,
    UpsertSubnet,
)
from sdn_controller.core.use_cases.networks import (
    AssignNetworkToNodes,
    CreateNetwork,
    GetNetwork,
    ListNetworks,
    UpdateNetwork,
)
from sdn_controller.core.use_cases.nodes import (
    GetNode,
    ListNodes,
    RegisterNode,
    RemoveNode,
)
from sdn_controller.core.use_cases.operations import GetOperation, ListOperations
from sdn_controller.core.use_cases.reconcile import ApplyNetwork
from sdn_controller.core.use_cases.topology import GetTopology, ScanDrift
from sdn_controller.core.value_objects.ids import IdFactory, UuidIdFactory
from sdn_controller.ports.agent import AgentPort
from sdn_controller.ports.persistence import (
    EnrollmentTokenRepository,
    IpAllocationRepository,
    NetworkRepository,
    NodeRepository,
    ObservedStateRepository,
    OperationRepository,
)
from sdn_controller.ports.security import TokenFactory


@dataclass(slots=True)
class Container:
    """Resolved dependency graph for the running application."""

    settings: Settings
    clock: Clock
    ids: IdFactory
    token_factory: TokenFactory
    agent: AgentPort
    planner: Planner

    nodes_repo: NodeRepository
    networks_repo: NetworkRepository
    operations_repo: OperationRepository
    enrollment_tokens_repo: EnrollmentTokenRepository
    observed_states_repo: ObservedStateRepository
    ip_allocations_repo: IpAllocationRepository

    create_network: CreateNetwork
    update_network: UpdateNetwork
    assign_network_to_nodes: AssignNetworkToNodes
    apply_network: ApplyNetwork
    list_networks: ListNetworks
    get_network: GetNetwork
    list_nodes: ListNodes
    get_node: GetNode
    register_node: RegisterNode
    remove_node: RemoveNode
    issue_enrollment_token: IssueEnrollmentToken
    enroll_agent: EnrollAgent
    record_heartbeat: RecordHeartbeat
    list_operations: ListOperations
    get_operation: GetOperation
    upsert_subnet: UpsertSubnet
    list_subnets: ListSubnets
    get_subnet: GetSubnet
    allocate_ip: AllocateIp
    reserve_ip: ReserveIp
    release_ip: ReleaseIp
    list_allocations: ListAllocations
    get_allocation: GetAllocation
    get_topology: GetTopology
    scan_drift: ScanDrift

    # Owned resources that need cleanup on shutdown (e.g. AsyncEngine).
    _shutdown_hooks: list[AsyncEngine] = field(default_factory=list)

    async def shutdown(self) -> None:
        for engine in self._shutdown_hooks:
            await engine.dispose()
        self._shutdown_hooks.clear()


def build_container(
    settings: Settings,
    *,
    agent: AgentPort | None = None,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
    token_factory: TokenFactory | None = None,
) -> Container:
    """Wire concrete adapters and use cases for the given settings.

    All four overrides default to production picks. Tests pass deterministic
    substitutes (frozen clock, counting id factory, sequential token factory,
    in-process FakeAgent) to keep assertions readable.
    """

    clock = clock if clock is not None else SystemClock()
    ids = ids if ids is not None else UuidIdFactory()
    token_factory = token_factory if token_factory is not None else SecretsTokenFactory()
    planner = Planner(ids=ids)
    agent = agent if agent is not None else FakeAgent(clock=clock)

    repos, shutdown_hooks = _build_repositories(settings)
    (
        nodes_repo,
        networks_repo,
        operations_repo,
        enrollment_tokens_repo,
        observed_states_repo,
        ip_allocations_repo,
    ) = repos

    return Container(
        settings=settings,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        agent=agent,
        planner=planner,
        nodes_repo=nodes_repo,
        networks_repo=networks_repo,
        operations_repo=operations_repo,
        enrollment_tokens_repo=enrollment_tokens_repo,
        observed_states_repo=observed_states_repo,
        ip_allocations_repo=ip_allocations_repo,
        create_network=CreateNetwork(
            networks=networks_repo,
            operations=operations_repo,
            clock=clock,
            ids=ids,
        ),
        update_network=UpdateNetwork(
            networks=networks_repo,
            operations=operations_repo,
            clock=clock,
            ids=ids,
        ),
        assign_network_to_nodes=AssignNetworkToNodes(
            networks=networks_repo,
            nodes=nodes_repo,
            operations=operations_repo,
            clock=clock,
            ids=ids,
        ),
        apply_network=ApplyNetwork(
            networks=networks_repo,
            nodes=nodes_repo,
            observed_states=observed_states_repo,
            operations=operations_repo,
            planner=planner,
            agent=agent,
            clock=clock,
            ids=ids,
        ),
        list_networks=ListNetworks(networks=networks_repo),
        get_network=GetNetwork(networks=networks_repo),
        list_nodes=ListNodes(
            nodes=nodes_repo,
            clock=clock,
            stale_after_seconds=settings.node_stale_after_seconds,
            offline_after_seconds=settings.node_offline_after_seconds,
        ),
        get_node=GetNode(
            nodes=nodes_repo,
            clock=clock,
            stale_after_seconds=settings.node_stale_after_seconds,
            offline_after_seconds=settings.node_offline_after_seconds,
        ),
        register_node=RegisterNode(
            nodes=nodes_repo,
            operations=operations_repo,
            clock=clock,
            ids=ids,
        ),
        remove_node=RemoveNode(
            nodes=nodes_repo,
            operations=operations_repo,
            clock=clock,
            ids=ids,
        ),
        issue_enrollment_token=IssueEnrollmentToken(
            nodes=nodes_repo,
            tokens=enrollment_tokens_repo,
            clock=clock,
            ids=ids,
            token_factory=token_factory,
            ttl_seconds=settings.enrollment_token_ttl_seconds,
        ),
        enroll_agent=EnrollAgent(
            nodes=nodes_repo,
            tokens=enrollment_tokens_repo,
            clock=clock,
        ),
        record_heartbeat=RecordHeartbeat(
            nodes=nodes_repo,
            clock=clock,
        ),
        list_operations=ListOperations(operations=operations_repo),
        get_operation=GetOperation(operations=operations_repo),
        upsert_subnet=UpsertSubnet(
            networks=networks_repo,
            allocations=ip_allocations_repo,
            ids=ids,
            clock=clock,
        ),
        list_subnets=ListSubnets(networks=networks_repo),
        get_subnet=GetSubnet(networks=networks_repo),
        allocate_ip=AllocateIp(
            networks=networks_repo,
            allocations=ip_allocations_repo,
            ids=ids,
            clock=clock,
        ),
        reserve_ip=ReserveIp(
            networks=networks_repo,
            allocations=ip_allocations_repo,
            ids=ids,
            clock=clock,
        ),
        release_ip=ReleaseIp(allocations=ip_allocations_repo),
        list_allocations=ListAllocations(networks=networks_repo, allocations=ip_allocations_repo),
        get_allocation=GetAllocation(allocations=ip_allocations_repo),
        get_topology=GetTopology(
            nodes=nodes_repo,
            networks=networks_repo,
            observed_states=observed_states_repo,
            clock=clock,
        ),
        scan_drift=ScanDrift(
            nodes=nodes_repo,
            networks=networks_repo,
            observed_states=observed_states_repo,
            clock=clock,
        ),
        _shutdown_hooks=shutdown_hooks,
    )


_RepoBundle = tuple[
    NodeRepository,
    NetworkRepository,
    OperationRepository,
    EnrollmentTokenRepository,
    ObservedStateRepository,
    IpAllocationRepository,
]


def _build_repositories(settings: Settings) -> tuple[_RepoBundle, list[AsyncEngine]]:
    """Pick the persistence adapter based on settings.

    Returns the six repositories plus the list of resources the container
    must close on shutdown (currently: the SQLAlchemy engine, if any).
    """
    if settings.persistence == "memory":
        return (
            (
                InMemoryNodeRepository(),
                InMemoryNetworkRepository(),
                InMemoryOperationRepository(),
                InMemoryEnrollmentTokenRepository(),
                InMemoryObservedStateRepository(),
                InMemoryIpAllocationRepository(),
            ),
            [],
        )

    if settings.persistence in {"sqlite", "postgres"}:
        engine = build_engine(settings.database_url, echo=settings.database_echo)
        sessionmaker = build_sessionmaker(engine)
        return (
            (
                SqlNodeRepository(sessionmaker),
                SqlNetworkRepository(sessionmaker),
                SqlOperationRepository(sessionmaker),
                SqlEnrollmentTokenRepository(sessionmaker),
                SqlObservedStateRepository(sessionmaker),
                SqlIpAllocationRepository(sessionmaker),
            ),
            [engine],
        )

    raise NotImplementedError(f"unsupported persistence backend: {settings.persistence!r}")
