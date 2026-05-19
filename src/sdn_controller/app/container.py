"""Dependency container.

The container builds repositories, services and use cases once at startup and
exposes them to HTTP handlers. We deliberately avoid a heavy DI framework —
constructor injection with a hand-written wiring layer is enough for now and
makes the dependency graph trivially auditable.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from sdn_controller import __version__
from sdn_controller.adapters.audit_archive import FileAuditArchive, NoopAuditArchive
from sdn_controller.adapters.locks import InMemoryLockStore, SqlLockStore
from sdn_controller.adapters.memory import (
    InMemoryAuditEventRepository,
    InMemoryEnrollmentTokenRepository,
    InMemoryIpAllocationRepository,
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryNodeSnapshotRepository,
    InMemoryObservedStateRepository,
    InMemoryOperationRepository,
    InMemoryOutboxRepository,
    InMemoryServiceAccountRepository,
    InMemoryServiceTokenRepository,
    InMemoryWebhookSubscriptionRepository,
)
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.adapters.secret_store import (
    FernetSecretStore,
    InMemorySecretStore,
)
from sdn_controller.adapters.security import SecretsTokenFactory
from sdn_controller.adapters.sql import (
    SqlAuditEventRepository,
    SqlEnrollmentTokenRepository,
    SqlIpAllocationRepository,
    SqlNetworkRepository,
    SqlNodeRepository,
    SqlNodeSnapshotRepository,
    SqlObservedStateRepository,
    SqlOperationRepository,
    SqlOutboxRepository,
    SqlServiceAccountRepository,
    SqlServiceTokenRepository,
    SqlWebhookSubscriptionRepository,
    build_engine,
    build_sessionmaker,
)
from sdn_controller.adapters.webhook import (
    HttpWebhookSender,
    InMemoryWebhookSender,
)
from sdn_controller.app.config import Settings
from sdn_controller.core.entities import ServiceToken, hash_service_token
from sdn_controller.core.services.clock import Clock, SystemClock
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.services.planner import Planner
from sdn_controller.core.use_cases.audit import ListAuditEvents, RecordAudit
from sdn_controller.core.use_cases.background import (
    HeartbeatReaper,
    ReconcilerSweep,
    RetentionSweep,
)
from sdn_controller.core.use_cases.backup import ExportBundle, ImportBundle
from sdn_controller.core.use_cases.enrollment import (
    EnrollAgent,
    IssueEnrollmentToken,
    RecordHeartbeat,
)
from sdn_controller.core.use_cases.events import ExportSnapshot, ListEvents
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
from sdn_controller.core.use_cases.node_snapshots import (
    GetNodeSnapshot,
    ListNodeSnapshots,
    RestoreNodeSnapshot,
    TakeNodeSnapshot,
)
from sdn_controller.core.use_cases.nodes import (
    GetNode,
    ListNodes,
    RegisterNode,
    RemoveNode,
)
from sdn_controller.core.use_cases.operations import GetOperation, ListOperations
from sdn_controller.core.use_cases.reconcile import ApplyNetwork
from sdn_controller.core.use_cases.service_accounts import (
    AuthenticatePrincipal,
    CreateServiceAccount,
    CreateServiceAccountCommand,
    DisableServiceAccount,
    GetServiceAccount,
    IssueServiceToken,
    ListServiceAccounts,
    ListServiceTokens,
    RevokeServiceToken,
)
from sdn_controller.core.use_cases.topology import GetTopology, ScanDrift
from sdn_controller.core.use_cases.webhooks import (
    CreateWebhookSubscription,
    DeleteWebhookSubscription,
    DispatchWebhooks,
    GetWebhookSubscription,
    ListWebhookSubscriptions,
)
from sdn_controller.core.value_objects.ids import IdFactory, UuidIdFactory
from sdn_controller.core.value_objects.security import Role
from sdn_controller.ports.agent import AgentPort
from sdn_controller.ports.audit_archive import AuditArchive
from sdn_controller.ports.locks import LockStore
from sdn_controller.ports.persistence import (
    AuditEventRepository,
    EnrollmentTokenRepository,
    IpAllocationRepository,
    NetworkRepository,
    NodeRepository,
    NodeSnapshotRepository,
    ObservedStateRepository,
    OperationRepository,
    OutboxRepository,
    ServiceAccountRepository,
    ServiceTokenRepository,
    WebhookSubscriptionRepository,
)
from sdn_controller.ports.secret_store import SecretStore
from sdn_controller.ports.security import TokenFactory
from sdn_controller.ports.webhook_sender import WebhookSender


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
    service_accounts_repo: ServiceAccountRepository
    service_tokens_repo: ServiceTokenRepository
    audit_events_repo: AuditEventRepository
    node_snapshots_repo: NodeSnapshotRepository
    outbox_repo: OutboxRepository
    webhook_subscriptions_repo: WebhookSubscriptionRepository

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
    authenticate_principal: AuthenticatePrincipal
    create_service_account: CreateServiceAccount
    list_service_accounts: ListServiceAccounts
    get_service_account: GetServiceAccount
    disable_service_account: DisableServiceAccount
    issue_service_token: IssueServiceToken
    revoke_service_token: RevokeServiceToken
    list_service_tokens: ListServiceTokens
    record_audit: RecordAudit
    list_audit_events: ListAuditEvents
    export_bundle: ExportBundle
    import_bundle: ImportBundle
    take_node_snapshot: TakeNodeSnapshot
    list_node_snapshots: ListNodeSnapshots
    get_node_snapshot: GetNodeSnapshot
    restore_node_snapshot: RestoreNodeSnapshot
    reconciler_sweep: ReconcilerSweep
    heartbeat_reaper: HeartbeatReaper
    retention_sweep: RetentionSweep
    audit_archive: AuditArchive
    locks: LockStore
    events: EventPublisher
    signer_store: SecretStore
    webhook_sender: WebhookSender
    create_webhook_subscription: CreateWebhookSubscription
    list_webhook_subscriptions: ListWebhookSubscriptions
    get_webhook_subscription: GetWebhookSubscription
    delete_webhook_subscription: DeleteWebhookSubscription
    dispatch_webhooks: DispatchWebhooks
    export_snapshot: ExportSnapshot
    list_events: ListEvents

    # Owned resources that need cleanup on shutdown (e.g. AsyncEngine).
    _shutdown_hooks: list[AsyncEngine] = field(default_factory=list)
    _background_tasks: list[asyncio.Task[None]] = field(default_factory=list)

    async def shutdown(self) -> None:
        for engine in self._shutdown_hooks:
            await engine.dispose()
        self._shutdown_hooks.clear()

    def start_background_tasks(self) -> None:
        """Запуск долгоиграющих task'ов (SDN-038, SDN-040).

        Стартует ровно три цикла; интервалы — из ``Settings``. Каждая
        ошибка внутри прохода логируется warning'ом, но цикл не
        ломается — иначе при первой же flaky-проблеме контроллер
        перестанет reconcile'ить.
        """
        if self._background_tasks:
            return
        self._background_tasks.append(
            asyncio.create_task(
                _periodic(
                    name="reconciler_sweep",
                    interval=self.settings.reconciler_interval_seconds,
                    fn=self.reconciler_sweep.execute,
                )
            )
        )
        self._background_tasks.append(
            asyncio.create_task(
                _periodic(
                    name="heartbeat_reaper",
                    interval=self.settings.heartbeat_reaper_interval_seconds,
                    fn=self.heartbeat_reaper.execute,
                )
            )
        )
        self._background_tasks.append(
            asyncio.create_task(
                _periodic(
                    name="retention_sweep",
                    interval=self.settings.retention_interval_seconds,
                    fn=self.retention_sweep.execute,
                )
            )
        )
        self._background_tasks.append(
            asyncio.create_task(
                _periodic(
                    name="webhook_dispatch",
                    interval=self.settings.webhook_dispatch_interval_seconds,
                    fn=self.dispatch_webhooks.execute,
                )
            )
        )

    async def stop_background_tasks(self) -> None:
        for task in self._background_tasks:
            task.cancel()
        for task in self._background_tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._background_tasks.clear()

    async def readiness_check(self) -> None:
        """Проверка готовности к приёму трафика (используется ``/readyz``).

        Для in-memory persistence — no-op (always ready). Для
        SQL-бэкендов — ``SELECT 1`` через тот же sessionmaker,
        которым пользуется приложение. Любой raise здесь
        транслируется в ``503 Service Unavailable``.
        """
        for engine in self._shutdown_hooks:
            # AsyncEngine.connect() сам по себе достаточен — он берёт
            # коннекшен из пула и проверяет, что он живой.
            async with engine.connect() as conn:
                await conn.exec_driver_sql("SELECT 1")

    async def bootstrap(self) -> None:
        """Идемпотентные шаги при первом старте — создаём admin
        service account и закрепляем за ним bootstrap-токен из настроек."""
        plaintext = self.settings.auth_bootstrap_admin_token
        if not plaintext:
            return

        name = self.settings.auth_bootstrap_admin_name
        existing = await self.service_accounts_repo.get_by_name(name)
        if existing is None:
            existing = await self.create_service_account.execute(
                CreateServiceAccountCommand(
                    name=name,
                    role=Role.ADMIN,
                    description=(
                        "Bootstrap administrator (created from SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN)"
                    ),
                    created_by="bootstrap",
                )
            )

        token_hash = hash_service_token(plaintext)
        if await self.service_tokens_repo.get_by_hash(token_hash) is not None:
            # Тот же plaintext уже зарегистрирован — ничего не делаем.
            return

        # Используем IssueServiceToken-логику, но мимо обычного pipeline'а:
        # plaintext задан оператором, нам нужно сохранить ровно его хэш.
        now = self.clock.now()
        token = ServiceToken(
            id=self.ids.service_token(),
            service_account_id=existing.id,
            token_hash=token_hash,
            issued_at=now,
            issued_by="bootstrap",
            label="bootstrap admin token",
        )
        await self.service_tokens_repo.save(token)


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

    repos, lock_store, shutdown_hooks = _build_repositories(settings, clock=clock)
    (
        nodes_repo,
        networks_repo,
        operations_repo,
        enrollment_tokens_repo,
        observed_states_repo,
        ip_allocations_repo,
        service_accounts_repo,
        service_tokens_repo,
        audit_events_repo,
        node_snapshots_repo,
        outbox_repo,
        webhook_subscriptions_repo,
    ) = repos
    events = EventPublisher(outbox=outbox_repo, clock=clock, ids=ids)
    signer_store: SecretStore = _build_secret_store(settings)
    webhook_sender: WebhookSender = (
        InMemoryWebhookSender()
        if settings.persistence == "memory" and settings.webhooks_use_inmemory_sender
        else HttpWebhookSender(timeout_seconds=settings.webhook_request_timeout_seconds)
    )

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
        service_accounts_repo=service_accounts_repo,
        service_tokens_repo=service_tokens_repo,
        audit_events_repo=audit_events_repo,
        node_snapshots_repo=node_snapshots_repo,
        outbox_repo=outbox_repo,
        create_network=CreateNetwork(
            networks=networks_repo,
            operations=operations_repo,
            clock=clock,
            ids=ids,
            events=events,
        ),
        update_network=UpdateNetwork(
            networks=networks_repo,
            operations=operations_repo,
            clock=clock,
            ids=ids,
            events=events,
        ),
        assign_network_to_nodes=AssignNetworkToNodes(
            networks=networks_repo,
            nodes=nodes_repo,
            operations=operations_repo,
            clock=clock,
            ids=ids,
            events=events,
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
            locks=lock_store,
            events=events,
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
            events=events,
        ),
        remove_node=RemoveNode(
            nodes=nodes_repo,
            operations=operations_repo,
            clock=clock,
            ids=ids,
            events=events,
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
            events=events,
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
        authenticate_principal=AuthenticatePrincipal(
            accounts=service_accounts_repo,
            tokens=service_tokens_repo,
            clock=clock,
        ),
        create_service_account=CreateServiceAccount(
            accounts=service_accounts_repo,
            clock=clock,
            ids=ids,
        ),
        list_service_accounts=ListServiceAccounts(accounts=service_accounts_repo),
        get_service_account=GetServiceAccount(accounts=service_accounts_repo),
        disable_service_account=DisableServiceAccount(
            accounts=service_accounts_repo,
            clock=clock,
        ),
        issue_service_token=IssueServiceToken(
            accounts=service_accounts_repo,
            tokens=service_tokens_repo,
            clock=clock,
            ids=ids,
            token_factory=token_factory,
        ),
        revoke_service_token=RevokeServiceToken(
            tokens=service_tokens_repo,
            clock=clock,
        ),
        list_service_tokens=ListServiceTokens(
            accounts=service_accounts_repo,
            tokens=service_tokens_repo,
        ),
        record_audit=RecordAudit(
            audit_events=audit_events_repo,
            clock=clock,
            ids=ids,
        ),
        list_audit_events=ListAuditEvents(audit_events=audit_events_repo),
        export_bundle=ExportBundle(
            networks=networks_repo,
            nodes=nodes_repo,
            service_accounts=service_accounts_repo,
            ip_allocations=ip_allocations_repo,
            audit_events=audit_events_repo,
            clock=clock,
            controller_version=__version__,
        ),
        import_bundle=ImportBundle(
            networks=networks_repo,
            nodes=nodes_repo,
            service_accounts=service_accounts_repo,
            ip_allocations=ip_allocations_repo,
            audit_events=audit_events_repo,
        ),
        take_node_snapshot=TakeNodeSnapshot(
            nodes=nodes_repo,
            snapshots=node_snapshots_repo,
            agent=agent,
            clock=clock,
            ids=ids,
        ),
        list_node_snapshots=ListNodeSnapshots(
            nodes=nodes_repo,
            snapshots=node_snapshots_repo,
        ),
        get_node_snapshot=GetNodeSnapshot(snapshots=node_snapshots_repo),
        restore_node_snapshot=RestoreNodeSnapshot(
            snapshots=node_snapshots_repo,
            agent=agent,
        ),
        reconciler_sweep=ReconcilerSweep(
            scan_drift=ScanDrift(
                nodes=nodes_repo,
                networks=networks_repo,
                observed_states=observed_states_repo,
                clock=clock,
            ),
            networks=networks_repo,
            apply_network=ApplyNetwork(
                networks=networks_repo,
                nodes=nodes_repo,
                observed_states=observed_states_repo,
                operations=operations_repo,
                planner=planner,
                agent=agent,
                clock=clock,
                ids=ids,
                locks=lock_store,
                events=events,
            ),
            auto_apply=settings.reconciler_auto_apply,
        ),
        heartbeat_reaper=HeartbeatReaper(
            nodes=nodes_repo,
            clock=clock,
            stale_after_seconds=settings.node_stale_after_seconds,
            offline_after_seconds=settings.node_offline_after_seconds,
        ),
        retention_sweep=RetentionSweep(
            operations=operations_repo,
            audit_events=audit_events_repo,
            audit_archive=_build_audit_archive(settings),
            clock=clock,
            operation_retention_days=settings.operation_retention_days,
            audit_retention_days=settings.audit_retention_days,
        ),
        audit_archive=_build_audit_archive(settings),
        locks=lock_store,
        events=events,
        signer_store=signer_store,
        webhook_sender=webhook_sender,
        webhook_subscriptions_repo=webhook_subscriptions_repo,
        create_webhook_subscription=CreateWebhookSubscription(
            subscriptions=webhook_subscriptions_repo,
            outbox=outbox_repo,
            signer_store=signer_store,
            clock=clock,
            ids=ids,
        ),
        list_webhook_subscriptions=ListWebhookSubscriptions(
            subscriptions=webhook_subscriptions_repo,
        ),
        get_webhook_subscription=GetWebhookSubscription(
            subscriptions=webhook_subscriptions_repo,
        ),
        delete_webhook_subscription=DeleteWebhookSubscription(
            subscriptions=webhook_subscriptions_repo,
            signer_store=signer_store,
        ),
        dispatch_webhooks=DispatchWebhooks(
            subscriptions=webhook_subscriptions_repo,
            outbox=outbox_repo,
            sender=webhook_sender,
            signer_store=signer_store,
            clock=clock,
            batch_size=settings.webhook_batch_size,
            max_failures=settings.webhook_max_failures,
        ),
        export_snapshot=ExportSnapshot(
            outbox=outbox_repo,
            networks=networks_repo,
            nodes=nodes_repo,
        ),
        list_events=ListEvents(outbox=outbox_repo),
        _shutdown_hooks=shutdown_hooks,
    )


_RepoBundle = tuple[
    NodeRepository,
    NetworkRepository,
    OperationRepository,
    EnrollmentTokenRepository,
    ObservedStateRepository,
    IpAllocationRepository,
    ServiceAccountRepository,
    ServiceTokenRepository,
    AuditEventRepository,
    NodeSnapshotRepository,
    OutboxRepository,
    WebhookSubscriptionRepository,
]


def _build_repositories(
    settings: Settings, *, clock: Clock
) -> tuple[_RepoBundle, LockStore, list[AsyncEngine]]:
    """Pick the persistence adapter based on settings.

    Returns (repo-bundle, LockStore, shutdown hooks). ``LockStore``
    отдаётся рядом с репами — он привязан к тому же бэкенду:
    in-memory дёргает свой dict, sql — таблицу ``operation_locks``.
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
                InMemoryServiceAccountRepository(),
                InMemoryServiceTokenRepository(),
                InMemoryAuditEventRepository(),
                InMemoryNodeSnapshotRepository(),
                InMemoryOutboxRepository(),
                InMemoryWebhookSubscriptionRepository(),
            ),
            InMemoryLockStore(clock=clock),
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
                SqlServiceAccountRepository(sessionmaker),
                SqlServiceTokenRepository(sessionmaker),
                SqlAuditEventRepository(sessionmaker),
                SqlNodeSnapshotRepository(sessionmaker),
                SqlOutboxRepository(sessionmaker),
                SqlWebhookSubscriptionRepository(sessionmaker),
            ),
            SqlLockStore(sessionmaker, clock=clock),
            [engine],
        )

    raise NotImplementedError(f"unsupported persistence backend: {settings.persistence!r}")


# ---------------------------------------------------------------------------
# Helpers (M13)
# ---------------------------------------------------------------------------

_periodic_log = structlog.get_logger("sdn_controller.background")


async def _periodic(
    *,
    name: str,
    interval: float,
    fn: Callable[[], Awaitable[Any]],
) -> None:
    """Бесконечный цикл «жди → выполни → лог». Любая ошибка ловится и
    логируется, цикл живёт дальше. ``CancelledError`` пробрасывается."""
    while True:
        try:
            result = await fn()
            _periodic_log.info("background_task_tick", task=name, result=str(result))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _periodic_log.warning("background_task_failed", task=name, error=str(exc))
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def _build_audit_archive(settings: Settings) -> AuditArchive:
    if settings.audit_archive_backend == "file":
        directory = settings.audit_archive_directory or "/var/lib/sdn-controller/audit-archive"
        return FileAuditArchive(directory=directory)
    return NoopAuditArchive()


def _build_secret_store(settings: Settings) -> SecretStore:
    """``SDN_SECRET_STORE_BACKEND`` — ``memory`` (default) или ``file``.

    Для ``file`` обязательны ``secret_store_path`` и
    ``secret_store_key`` (Fernet, 32-byte url-safe base64). Без ключа —
    raise: лучше упасть на старте, чем работать в режиме «процесс с
    плейнтекстом, который никогда не доедет до диска».
    """
    if settings.secret_store_backend == "memory":  # noqa: S105 — enum literal
        return InMemorySecretStore()
    if settings.secret_store_backend == "file":  # noqa: S105 — enum literal
        if not settings.secret_store_key:
            raise RuntimeError(
                "SDN_SECRET_STORE_KEY must be set when SDN_SECRET_STORE_BACKEND='file'",
            )
        path = settings.secret_store_path or "/var/lib/sdn-controller/secret-store/store.enc"
        return FernetSecretStore(path=path, master_key=settings.secret_store_key)
    raise NotImplementedError(
        f"unsupported secret store backend: {settings.secret_store_backend!r}",
    )
