"""Use cases N4 — Governance & Scale.

N4-01  Limits & Usage: ProjectQuota CRUD + QuotaService enforcement
N4-02  Preflight checks: PreflightRouter перед ApplyRouter
N4-03  Snapshot v2: мультиресурсный версионированный снапшот
N4-04  Gateway HA: GatewayBond CRUD + ApplyGatewayBond (BondConfigurator)
N4-05  Retention policies: настраиваемое время хранения данных
N4-06  LBaaS: LoadBalancer / LbListener / LbPool / LbMember CRUD + ApplyLoadBalancer
N4-07  Health Monitor: HealthMonitor CRUD для пулов LB
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sdn_controller.core.entities.gateway_bond import GatewayBond
from sdn_controller.core.entities.health_monitor import HealthMonitor
from sdn_controller.core.entities.load_balancer import (
    LbListener,
    LbMember,
    LbPool,
    LoadBalancer,
)
from sdn_controller.core.entities.project_quota import ProjectQuota
from sdn_controller.core.entities.resource_snapshot import ResourceSnapshot
from sdn_controller.core.entities.retention_policy import RetentionPolicy
from sdn_controller.core.services.bond_configurator import BondConfigurator
from sdn_controller.core.services.lb_configurator import LbConfigurator
from sdn_controller.core.services.preflight_checker import PreflightChecker
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.value_objects.enums import (
    BondMode,
    HealthCheckType,
    LbAlgorithm,
    LbProtocol,
    QuotaResource,
    RetentionScope,
    SessionPersistence,
)
from sdn_controller.core.value_objects.errors import NotFoundError, ValidationError
from sdn_controller.core.value_objects.ids import (
    GatewayBondId,
    HealthMonitorId,
    IdFactory,
    LbListenerId,
    LbMemberId,
    LbPoolId,
    LoadBalancerId,
    NetworkId,
    NodeId,
    ProjectId,
    ProjectQuotaId,
    ResourceSnapshotId,
    RetentionPolicyId,
    RouterId,
)
from sdn_controller.ports.persistence import (
    GatewayBondRepository,
    HealthMonitorRepository,
    LbListenerRepository,
    LbMemberRepository,
    LbPoolRepository,
    LoadBalancerRepository,
    ProjectQuotaRepository,
    ResourceSnapshotRepository,
    RetentionPolicyRepository,
    RouterRepository,
)

__all__ = [
    # N4-01
    "SetProjectQuotaCommand",
    "SetProjectQuota",
    "GetProjectQuota",
    "DeleteProjectQuota",
    "CheckProjectUsage",
    # N4-02
    "RunPreflightRouter",
    # N4-03
    "TakeResourceSnapshotCommand",
    "TakeResourceSnapshot",
    "GetResourceSnapshot",
    "ListResourceSnapshots",
    "DeleteResourceSnapshot",
    # N4-04
    "CreateGatewayBondCommand",
    "UpdateGatewayBondCommand",
    "CreateGatewayBond",
    "GetGatewayBond",
    "ListGatewayBonds",
    "UpdateGatewayBond",
    "DeleteGatewayBond",
    "ApplyGatewayBond",
    # N4-05
    "SetRetentionPolicyCommand",
    "SetRetentionPolicy",
    "GetRetentionPolicy",
    "ListRetentionPolicies",
    "DeleteRetentionPolicy",
    # N4-06
    "CreateLoadBalancerCommand",
    "CreateLbListenerCommand",
    "CreateLbPoolCommand",
    "AddLbMemberCommand",
    "UpdateLbPoolCommand",
    "CreateLoadBalancer",
    "GetLoadBalancer",
    "ListLoadBalancers",
    "UpdateLoadBalancer",
    "DeleteLoadBalancer",
    "ApplyLoadBalancer",
    "SetLbAdminState",
    "CreateLbListener",
    "GetLbListener",
    "ListLbListeners",
    "UpdateLbListener",
    "DeleteLbListener",
    "CreateLbPool",
    "GetLbPool",
    "ListLbPools",
    "UpdateLbPool",
    "DeleteLbPool",
    "AddLbMember",
    "GetLbMember",
    "ListLbMembers",
    "UpdateLbMember",
    "RemoveLbMember",
    # N4-07
    "CreateHealthMonitorCommand",
    "UpdateHealthMonitorCommand",
    "CreateHealthMonitor",
    "GetHealthMonitor",
    "UpdateHealthMonitor",
    "DeleteHealthMonitor",
]


# ---------------------------------------------------------------------------
# N4-01  ProjectQuota
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetProjectQuotaCommand:
    """Устанавливает или обновляет лимит одного ресурса для проекта."""

    project_id: ProjectId
    resource: str           # значение QuotaResource
    limit: int | None       # None = снять ограничение


class SetProjectQuota:
    """Устанавливает квоту ресурса для проекта (N4-01)."""

    def __init__(
        self,
        *,
        quotas: ProjectQuotaRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._quotas = quotas
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: SetProjectQuotaCommand) -> ProjectQuota:
        now = self._clock.now()
        resource = QuotaResource(cmd.resource)
        quota = await self._quotas.get_by_project(cmd.project_id)
        if quota is None:
            quota = ProjectQuota(
                id=self._ids.project_quota(),
                project_id=cmd.project_id,
                created_at=now,
                updated_at=now,
            )
        if cmd.limit is None:
            quota.remove_limit(resource, now=now)
        else:
            quota.set_limit(resource, cmd.limit, now=now)
        await self._quotas.save(quota)
        await self._events.publish(
            event_type="quota.updated",
            resource_type="project_quota",
            resource_id=quota.id,
            payload={"resource": cmd.resource, "limit": cmd.limit},
            project_id=cmd.project_id,
        )
        return quota


class GetProjectQuota:
    """Возвращает квоту проекта (N4-01)."""

    def __init__(self, *, quotas: ProjectQuotaRepository) -> None:
        self._quotas = quotas

    async def execute(self, project_id: ProjectId) -> ProjectQuota | None:
        return await self._quotas.get_by_project(project_id)


class DeleteProjectQuota:
    """Удаляет все квоты проекта (сброс к «без ограничений») (N4-01)."""

    def __init__(self, *, quotas: ProjectQuotaRepository, events: EventPublisher) -> None:
        self._quotas = quotas
        self._events = events

    async def execute(self, project_id: ProjectId) -> None:
        quota = await self._quotas.get_by_project(project_id)
        if quota is None:
            return
        await self._quotas.delete(quota.id)
        await self._events.publish(
            event_type="quota.deleted",
            resource_type="project_quota",
            resource_id=quota.id,
            project_id=project_id,
        )


class CheckProjectUsage:
    """Возвращает текущее использование ресурсов и нарушения квот (N4-01)."""

    def __init__(
        self,
        *,
        quotas: ProjectQuotaRepository,
        routers: RouterRepository,
        load_balancers: LoadBalancerRepository,
    ) -> None:
        self._quotas = quotas
        self._routers = routers
        self._load_balancers = load_balancers

    async def execute(self, project_id: ProjectId) -> dict[str, Any]:
        quota = await self._quotas.get_by_project(project_id)
        router_count = len(await self._routers.list(project_id=project_id))
        lb_count = len(await self._load_balancers.list(project_id=project_id))
        usage = {
            QuotaResource.ROUTERS.value: router_count,
            QuotaResource.LOAD_BALANCERS.value: lb_count,
        }
        violations: list[dict[str, Any]] = []
        if quota is not None:
            from sdn_controller.core.services.quota_service import QuotaService
            svc = QuotaService()
            for v in svc.compute_violations(quota, usage):
                violations.append({
                    "resource": v.resource.value,
                    "limit": v.limit,
                    "current": v.current,
                })
        return {
            "project_id": project_id,
            "usage": usage,
            "limits": quota.limits if quota else {},
            "violations": violations,
        }


# ---------------------------------------------------------------------------
# N4-02  Preflight checks
# ---------------------------------------------------------------------------


class RunPreflightRouter:
    """Запускает preflight-проверки маршрутизатора перед ApplyRouter (N4-02).

    Возвращает список проблем. Ошибки ERROR-уровня поднимают ValidationError.
    """

    def __init__(self, *, routers: RouterRepository) -> None:
        self._routers = routers
        self._checker = PreflightChecker()

    async def execute(self, router_id: RouterId) -> list[dict[str, str]]:
        router = await self._routers.get(router_id)
        if router is None:
            raise NotFoundError(f"маршрутизатор {router_id} не найден")
        issues = self._checker.check_router(router)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            messages = "; ".join(i.message for i in errors)
            raise ValidationError(f"preflight-проверка не пройдена: {messages}")
        return [
            {"severity": i.severity, "code": i.code, "message": i.message}
            for i in issues
        ]


# ---------------------------------------------------------------------------
# N4-03  ResourceSnapshot v2
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TakeResourceSnapshotCommand:
    project_id: ProjectId
    label: str = ""
    include_routers: bool = True
    include_networks: bool = True
    include_floating_ips: bool = True


class TakeResourceSnapshot:
    """Создаёт мультиресурсный снапшот проекта (N4-03)."""

    def __init__(
        self,
        *,
        snapshots: ResourceSnapshotRepository,
        routers: RouterRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._snapshots = snapshots
        self._routers = routers
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: TakeResourceSnapshotCommand) -> ResourceSnapshot:
        now = self._clock.now()
        # вычисляем следующую версию
        existing = await self._snapshots.list(project_id=cmd.project_id)
        version = (max((s.version for s in existing), default=0)) + 1

        payload: dict[str, Any] = {}
        resource_types: list[str] = []

        if cmd.include_routers:
            routers = await self._routers.list(project_id=cmd.project_id)
            payload["routers"] = [
                {
                    "id": r.id,
                    "name": r.name,
                    "status": r.status,
                    "external_network_id": r.external_network_id,
                    "internal_network_ids": list(r.internal_network_ids),
                    "ha_mode": r.ha_mode,
                }
                for r in routers
            ]
            resource_types.append("routers")

        snap = ResourceSnapshot(
            id=self._ids.resource_snapshot(),
            project_id=cmd.project_id,
            version=version,
            label=cmd.label,
            resource_types=resource_types,
            payload=payload,
            created_at=now,
        )
        await self._snapshots.save(snap)
        await self._events.publish(
            event_type="snapshot.created",
            resource_type="resource_snapshot",
            resource_id=snap.id,
            payload={"version": version, "label": cmd.label},
            project_id=cmd.project_id,
        )
        return snap


class GetResourceSnapshot:
    def __init__(self, *, snapshots: ResourceSnapshotRepository) -> None:
        self._snapshots = snapshots

    async def execute(self, snap_id: ResourceSnapshotId) -> ResourceSnapshot:
        snap = await self._snapshots.get(snap_id)
        if snap is None:
            raise NotFoundError(f"снапшот {snap_id} не найден")
        return snap


class ListResourceSnapshots:
    def __init__(self, *, snapshots: ResourceSnapshotRepository) -> None:
        self._snapshots = snapshots

    async def execute(self, *, project_id: ProjectId | None = None) -> list[ResourceSnapshot]:
        return await self._snapshots.list(project_id=project_id)


class DeleteResourceSnapshot:
    def __init__(self, *, snapshots: ResourceSnapshotRepository, events: EventPublisher) -> None:
        self._snapshots = snapshots
        self._events = events

    async def execute(self, snap_id: ResourceSnapshotId) -> None:
        snap = await self._snapshots.get(snap_id)
        if snap is None:
            raise NotFoundError(f"снапшот {snap_id} не найден")
        await self._snapshots.delete(snap_id)
        await self._events.publish(
            event_type="snapshot.deleted",
            resource_type="resource_snapshot",
            resource_id=snap_id,
            project_id=snap.project_id,
        )


# ---------------------------------------------------------------------------
# N4-04  GatewayBond
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateGatewayBondCommand:
    name: str
    node_id: NodeId
    bond_name: str
    mode: str = "none"
    members: list[str] | None = None
    mtu: int = 1500
    project_id: ProjectId | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class UpdateGatewayBondCommand:
    bond_id: GatewayBondId
    name: str | None = None
    mode: str | None = None
    members: list[str] | None = None
    mtu: int | None = None
    labels: dict[str, str] | None = None


class CreateGatewayBond:
    """Создаёт конфигурацию bonding-интерфейса на узле (N4-04)."""

    def __init__(
        self,
        *,
        bonds: GatewayBondRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._bonds = bonds
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateGatewayBondCommand) -> GatewayBond:
        now = self._clock.now()
        bond = GatewayBond(
            id=self._ids.gateway_bond(),
            name=cmd.name,
            node_id=cmd.node_id,
            bond_name=cmd.bond_name,
            mode=BondMode(cmd.mode),
            members=list(cmd.members or []),
            mtu=cmd.mtu,
            project_id=cmd.project_id,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._bonds.save(bond)
        await self._events.publish(
            event_type="gateway_bond.created",
            resource_type="gateway_bond",
            resource_id=bond.id,
            payload={"bond_name": bond.bond_name, "mode": bond.mode},
            project_id=bond.project_id,
        )
        return bond


class GetGatewayBond:
    def __init__(self, *, bonds: GatewayBondRepository) -> None:
        self._bonds = bonds

    async def execute(self, bond_id: GatewayBondId) -> GatewayBond:
        bond = await self._bonds.get(bond_id)
        if bond is None:
            raise NotFoundError(f"gateway bond {bond_id} не найден")
        return bond


class ListGatewayBonds:
    def __init__(self, *, bonds: GatewayBondRepository) -> None:
        self._bonds = bonds

    async def execute(
        self,
        *,
        node_id: NodeId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[GatewayBond]:
        return await self._bonds.list(node_id=node_id, project_id=project_id)


class UpdateGatewayBond:
    def __init__(
        self, *, bonds: GatewayBondRepository, clock: Clock, events: EventPublisher
    ) -> None:
        self._bonds = bonds
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateGatewayBondCommand) -> GatewayBond:
        bond = await self._bonds.get(cmd.bond_id)
        if bond is None:
            raise NotFoundError(f"gateway bond {cmd.bond_id} не найден")
        bond.update(
            name=cmd.name,
            mode=BondMode(cmd.mode) if cmd.mode else None,
            members=cmd.members,
            mtu=cmd.mtu,
            labels=cmd.labels,
            now=self._clock.now(),
        )
        await self._bonds.save(bond)
        await self._events.publish(
            event_type="gateway_bond.updated",
            resource_type="gateway_bond",
            resource_id=bond.id,
            project_id=bond.project_id,
        )
        return bond


class DeleteGatewayBond:
    def __init__(self, *, bonds: GatewayBondRepository, events: EventPublisher) -> None:
        self._bonds = bonds
        self._events = events

    async def execute(self, bond_id: GatewayBondId) -> None:
        bond = await self._bonds.get(bond_id)
        if bond is None:
            raise NotFoundError(f"gateway bond {bond_id} не найден")
        await self._bonds.delete(bond_id)
        await self._events.publish(
            event_type="gateway_bond.deleted",
            resource_type="gateway_bond",
            resource_id=bond_id,
            project_id=bond.project_id,
        )


class ApplyGatewayBond:
    """Генерирует netplan-конфиг для bond-интерфейса (N4-04)."""

    def __init__(
        self, *, bonds: GatewayBondRepository, clock: Clock, events: EventPublisher
    ) -> None:
        self._bonds = bonds
        self._clock = clock
        self._events = events
        self._configurator = BondConfigurator()

    async def execute(self, bond_id: GatewayBondId) -> GatewayBond:
        bond = await self._bonds.get(bond_id)
        if bond is None:
            raise NotFoundError(f"gateway bond {bond_id} не найден")
        now = self._clock.now()
        config = self._configurator.generate(bond, now=now)
        bond.mark_applied(config, now=now)
        await self._bonds.save(bond)
        await self._events.publish(
            event_type="gateway_bond.applied",
            resource_type="gateway_bond",
            resource_id=bond.id,
            project_id=bond.project_id,
        )
        return bond


# ---------------------------------------------------------------------------
# N4-05  RetentionPolicy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetRetentionPolicyCommand:
    scope: str              # значение RetentionScope
    retention_days: int
    project_id: ProjectId | None = None
    description: str = ""


class SetRetentionPolicy:
    """Создаёт или обновляет политику хранения данных (N4-05)."""

    def __init__(
        self,
        *,
        policies: RetentionPolicyRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: SetRetentionPolicyCommand) -> RetentionPolicy:
        now = self._clock.now()
        scope = RetentionScope(cmd.scope)
        existing = await self._policies.get_by_scope(
            scope=scope, project_id=cmd.project_id
        )
        if existing is not None:
            existing.update(
                retention_days=cmd.retention_days,
                description=cmd.description or None,
                now=now,
            )
            await self._policies.save(existing)
            await self._events.publish(
                event_type="retention_policy.updated",
                resource_type="retention_policy",
                resource_id=existing.id,
                project_id=cmd.project_id,
            )
            return existing

        policy = RetentionPolicy(
            id=self._ids.retention_policy(),
            scope=scope,
            retention_days=cmd.retention_days,
            project_id=cmd.project_id,
            description=cmd.description,
            created_at=now,
            updated_at=now,
        )
        await self._policies.save(policy)
        await self._events.publish(
            event_type="retention_policy.created",
            resource_type="retention_policy",
            resource_id=policy.id,
            project_id=cmd.project_id,
        )
        return policy


class GetRetentionPolicy:
    def __init__(self, *, policies: RetentionPolicyRepository) -> None:
        self._policies = policies

    async def execute(self, policy_id: RetentionPolicyId) -> RetentionPolicy:
        policy = await self._policies.get(policy_id)
        if policy is None:
            raise NotFoundError(f"политика хранения {policy_id} не найдена")
        return policy


class ListRetentionPolicies:
    def __init__(self, *, policies: RetentionPolicyRepository) -> None:
        self._policies = policies

    async def execute(self, *, project_id: ProjectId | None = None) -> list[RetentionPolicy]:
        return await self._policies.list(project_id=project_id)


class DeleteRetentionPolicy:
    def __init__(self, *, policies: RetentionPolicyRepository, events: EventPublisher) -> None:
        self._policies = policies
        self._events = events

    async def execute(self, policy_id: RetentionPolicyId) -> None:
        policy = await self._policies.get(policy_id)
        if policy is None:
            raise NotFoundError(f"политика хранения {policy_id} не найдена")
        await self._policies.delete(policy_id)
        await self._events.publish(
            event_type="retention_policy.deleted",
            resource_type="retention_policy",
            resource_id=policy_id,
            project_id=policy.project_id,
        )


# ---------------------------------------------------------------------------
# N4-06  LoadBalancer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateLoadBalancerCommand:
    name: str
    vip_address: str
    vip_network_id: NetworkId
    project_id: ProjectId | None = None
    router_id: RouterId | None = None
    description: str = ""
    provider: str = "haproxy"
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class CreateLbListenerCommand:
    name: str
    lb_id: LoadBalancerId
    protocol: str
    protocol_port: int
    default_pool_id: LbPoolId | None = None
    description: str = ""
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class CreateLbPoolCommand:
    name: str
    lb_id: LoadBalancerId
    protocol: str
    lb_algorithm: str = "round_robin"
    session_persistence: str = "none"
    description: str = ""
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class UpdateLbPoolCommand:
    pool_id: LbPoolId
    name: str | None = None
    lb_algorithm: str | None = None
    session_persistence: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class AddLbMemberCommand:
    pool_id: LbPoolId
    address: str
    protocol_port: int
    weight: int = 1
    admin_state_up: bool = True


class CreateLoadBalancer:
    """Создаёт балансировщик нагрузки (N4-06)."""

    def __init__(
        self,
        *,
        load_balancers: LoadBalancerRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._lbs = load_balancers
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateLoadBalancerCommand) -> LoadBalancer:
        now = self._clock.now()
        lb = LoadBalancer(
            id=self._ids.load_balancer(),
            name=cmd.name,
            vip_address=cmd.vip_address,
            vip_network_id=cmd.vip_network_id,
            project_id=cmd.project_id,
            router_id=cmd.router_id,
            description=cmd.description,
            provider=cmd.provider,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._lbs.save(lb)
        await self._events.publish(
            event_type="load_balancer.created",
            resource_type="load_balancer",
            resource_id=lb.id,
            payload={"name": lb.name, "vip_address": lb.vip_address},
            project_id=lb.project_id,
        )
        return lb


class GetLoadBalancer:
    def __init__(self, *, load_balancers: LoadBalancerRepository) -> None:
        self._lbs = load_balancers

    async def execute(self, lb_id: LoadBalancerId) -> LoadBalancer:
        lb = await self._lbs.get(lb_id)
        if lb is None:
            raise NotFoundError(f"балансировщик {lb_id} не найден")
        return lb


class ListLoadBalancers:
    def __init__(self, *, load_balancers: LoadBalancerRepository) -> None:
        self._lbs = load_balancers

    async def execute(self, *, project_id: ProjectId | None = None) -> list[LoadBalancer]:
        return await self._lbs.list(project_id=project_id)


class UpdateLoadBalancer:
    def __init__(
        self,
        *,
        load_balancers: LoadBalancerRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._lbs = load_balancers
        self._clock = clock
        self._events = events

    async def execute(
        self,
        lb_id: LoadBalancerId,
        *,
        name: str | None = None,
        description: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> LoadBalancer:
        lb = await self._lbs.get(lb_id)
        if lb is None:
            raise NotFoundError(f"балансировщик {lb_id} не найден")
        lb.update(name=name, description=description, labels=labels, now=self._clock.now())
        await self._lbs.save(lb)
        await self._events.publish(
            event_type="load_balancer.updated",
            resource_type="load_balancer",
            resource_id=lb.id,
            project_id=lb.project_id,
        )
        return lb


class DeleteLoadBalancer:
    def __init__(self, *, load_balancers: LoadBalancerRepository, events: EventPublisher) -> None:
        self._lbs = load_balancers
        self._events = events

    async def execute(self, lb_id: LoadBalancerId) -> None:
        lb = await self._lbs.get(lb_id)
        if lb is None:
            raise NotFoundError(f"балансировщик {lb_id} не найден")
        await self._lbs.delete(lb_id)
        await self._events.publish(
            event_type="load_balancer.deleted",
            resource_type="load_balancer",
            resource_id=lb_id,
            project_id=lb.project_id,
        )


class ApplyLoadBalancer:
    """Генерирует haproxy.cfg для балансировщика (N4-06).

    В MVP конфиг генерируется и сохраняется в applied_config;
    реального вызова агента нет — это задача N5+.
    """

    def __init__(
        self,
        *,
        load_balancers: LoadBalancerRepository,
        listeners: LbListenerRepository,
        pools: LbPoolRepository,
        members: LbMemberRepository,
        monitors: HealthMonitorRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._lbs = load_balancers
        self._listeners = listeners
        self._pools = pools
        self._members = members
        self._monitors = monitors
        self._clock = clock
        self._events = events
        self._configurator = LbConfigurator()

    async def execute(self, lb_id: LoadBalancerId) -> LoadBalancer:
        lb = await self._lbs.get(lb_id)
        if lb is None:
            raise NotFoundError(f"балансировщик {lb_id} не найден")
        lb_listeners = await self._listeners.list(lb_id=lb_id)
        lb_pools = await self._pools.list(lb_id=lb_id)
        members_map: dict[str, list[LbMember]] = {}
        monitors_map: dict[str, HealthMonitor] = {}
        for pool in lb_pools:
            members_map[pool.id] = await self._members.list(pool_id=pool.id)
            monitor = await self._monitors.get_by_pool(pool.id)
            if monitor is not None:
                monitors_map[pool.id] = monitor
        now = self._clock.now()
        config = self._configurator.generate(
            lb, lb_listeners, lb_pools, members_map, monitors_map, now=now
        )
        lb.mark_active(config, now=now)
        await self._lbs.save(lb)
        await self._events.publish(
            event_type="load_balancer.applied",
            resource_type="load_balancer",
            resource_id=lb.id,
            project_id=lb.project_id,
        )
        return lb


class SetLbAdminState:
    def __init__(
        self,
        *,
        load_balancers: LoadBalancerRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._lbs = load_balancers
        self._clock = clock
        self._events = events

    async def execute(self, lb_id: LoadBalancerId, *, up: bool) -> LoadBalancer:
        lb = await self._lbs.get(lb_id)
        if lb is None:
            raise NotFoundError(f"балансировщик {lb_id} не найден")
        lb.set_admin_state(up=up, now=self._clock.now())
        await self._lbs.save(lb)
        await self._events.publish(
            event_type="load_balancer.admin_state_changed",
            resource_type="load_balancer",
            resource_id=lb.id,
            payload={"admin_state_up": up},
            project_id=lb.project_id,
        )
        return lb


# LbListener use cases

class CreateLbListener:
    def __init__(
        self,
        *,
        listeners: LbListenerRepository,
        load_balancers: LoadBalancerRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._listeners = listeners
        self._lbs = load_balancers
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateLbListenerCommand) -> LbListener:
        if await self._lbs.get(cmd.lb_id) is None:
            raise NotFoundError(f"балансировщик {cmd.lb_id} не найден")
        now = self._clock.now()
        listener = LbListener(
            id=self._ids.lb_listener(),
            name=cmd.name,
            lb_id=cmd.lb_id,
            protocol=LbProtocol(cmd.protocol),
            protocol_port=cmd.protocol_port,
            default_pool_id=cmd.default_pool_id,
            description=cmd.description,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._listeners.save(listener)
        await self._events.publish(
            event_type="lb_listener.created",
            resource_type="lb_listener",
            resource_id=listener.id,
            project_id=None,
        )
        return listener


class GetLbListener:
    def __init__(self, *, listeners: LbListenerRepository) -> None:
        self._listeners = listeners

    async def execute(self, listener_id: LbListenerId) -> LbListener:
        l = await self._listeners.get(listener_id)
        if l is None:
            raise NotFoundError(f"listener {listener_id} не найден")
        return l


class ListLbListeners:
    def __init__(self, *, listeners: LbListenerRepository) -> None:
        self._listeners = listeners

    async def execute(self, *, lb_id: LoadBalancerId | None = None) -> list[LbListener]:
        return await self._listeners.list(lb_id=lb_id)


class UpdateLbListener:
    def __init__(
        self, *, listeners: LbListenerRepository, clock: Clock, events: EventPublisher
    ) -> None:
        self._listeners = listeners
        self._clock = clock
        self._events = events

    async def execute(
        self,
        listener_id: LbListenerId,
        *,
        name: str | None = None,
        default_pool_id: LbPoolId | None = None,
        description: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> LbListener:
        listener = await self._listeners.get(listener_id)
        if listener is None:
            raise NotFoundError(f"listener {listener_id} не найден")
        listener.update(
            name=name,
            default_pool_id=default_pool_id,
            description=description,
            labels=labels,
            now=self._clock.now(),
        )
        await self._listeners.save(listener)
        await self._events.publish(
            event_type="lb_listener.updated",
            resource_type="lb_listener",
            resource_id=listener.id,
            project_id=None,
        )
        return listener


class DeleteLbListener:
    def __init__(self, *, listeners: LbListenerRepository, events: EventPublisher) -> None:
        self._listeners = listeners
        self._events = events

    async def execute(self, listener_id: LbListenerId) -> None:
        listener = await self._listeners.get(listener_id)
        if listener is None:
            raise NotFoundError(f"listener {listener_id} не найден")
        await self._listeners.delete(listener_id)
        await self._events.publish(
            event_type="lb_listener.deleted",
            resource_type="lb_listener",
            resource_id=listener_id,
            project_id=None,
        )


# LbPool use cases

class CreateLbPool:
    def __init__(
        self,
        *,
        pools: LbPoolRepository,
        load_balancers: LoadBalancerRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._pools = pools
        self._lbs = load_balancers
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateLbPoolCommand) -> LbPool:
        if await self._lbs.get(cmd.lb_id) is None:
            raise NotFoundError(f"балансировщик {cmd.lb_id} не найден")
        now = self._clock.now()
        pool = LbPool(
            id=self._ids.lb_pool(),
            name=cmd.name,
            lb_id=cmd.lb_id,
            protocol=LbProtocol(cmd.protocol),
            lb_algorithm=LbAlgorithm(cmd.lb_algorithm),
            session_persistence=SessionPersistence(cmd.session_persistence),
            description=cmd.description,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._pools.save(pool)
        await self._events.publish(
            event_type="lb_pool.created",
            resource_type="lb_pool",
            resource_id=pool.id,
            project_id=None,
        )
        return pool


class GetLbPool:
    def __init__(self, *, pools: LbPoolRepository) -> None:
        self._pools = pools

    async def execute(self, pool_id: LbPoolId) -> LbPool:
        pool = await self._pools.get(pool_id)
        if pool is None:
            raise NotFoundError(f"пул {pool_id} не найден")
        return pool


class ListLbPools:
    def __init__(self, *, pools: LbPoolRepository) -> None:
        self._pools = pools

    async def execute(self, *, lb_id: LoadBalancerId | None = None) -> list[LbPool]:
        return await self._pools.list(lb_id=lb_id)


class UpdateLbPool:
    def __init__(
        self, *, pools: LbPoolRepository, clock: Clock, events: EventPublisher
    ) -> None:
        self._pools = pools
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateLbPoolCommand) -> LbPool:
        pool = await self._pools.get(cmd.pool_id)
        if pool is None:
            raise NotFoundError(f"пул {cmd.pool_id} не найден")
        pool.update(
            name=cmd.name,
            lb_algorithm=LbAlgorithm(cmd.lb_algorithm) if cmd.lb_algorithm else None,
            session_persistence=SessionPersistence(cmd.session_persistence)
            if cmd.session_persistence else None,
            description=cmd.description,
            labels=cmd.labels,
            now=self._clock.now(),
        )
        await self._pools.save(pool)
        await self._events.publish(
            event_type="lb_pool.updated",
            resource_type="lb_pool",
            resource_id=pool.id,
            project_id=None,
        )
        return pool


class DeleteLbPool:
    def __init__(self, *, pools: LbPoolRepository, events: EventPublisher) -> None:
        self._pools = pools
        self._events = events

    async def execute(self, pool_id: LbPoolId) -> None:
        pool = await self._pools.get(pool_id)
        if pool is None:
            raise NotFoundError(f"пул {pool_id} не найден")
        await self._pools.delete(pool_id)
        await self._events.publish(
            event_type="lb_pool.deleted",
            resource_type="lb_pool",
            resource_id=pool_id,
            project_id=None,
        )


# LbMember use cases

class AddLbMember:
    def __init__(
        self,
        *,
        members: LbMemberRepository,
        pools: LbPoolRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._members = members
        self._pools = pools
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: AddLbMemberCommand) -> LbMember:
        if await self._pools.get(cmd.pool_id) is None:
            raise NotFoundError(f"пул {cmd.pool_id} не найден")
        now = self._clock.now()
        member = LbMember(
            id=self._ids.lb_member(),
            pool_id=cmd.pool_id,
            address=cmd.address,
            protocol_port=cmd.protocol_port,
            weight=cmd.weight,
            admin_state_up=cmd.admin_state_up,
            created_at=now,
            updated_at=now,
        )
        await self._members.save(member)
        await self._events.publish(
            event_type="lb_member.added",
            resource_type="lb_member",
            resource_id=member.id,
            payload={"address": member.address, "port": member.protocol_port},
            project_id=None,
        )
        return member


class GetLbMember:
    def __init__(self, *, members: LbMemberRepository) -> None:
        self._members = members

    async def execute(self, member_id: LbMemberId) -> LbMember:
        m = await self._members.get(member_id)
        if m is None:
            raise NotFoundError(f"member {member_id} не найден")
        return m


class ListLbMembers:
    def __init__(self, *, members: LbMemberRepository) -> None:
        self._members = members

    async def execute(self, *, pool_id: LbPoolId | None = None) -> list[LbMember]:
        return await self._members.list(pool_id=pool_id)


class UpdateLbMember:
    def __init__(
        self, *, members: LbMemberRepository, clock: Clock, events: EventPublisher
    ) -> None:
        self._members = members
        self._clock = clock
        self._events = events

    async def execute(
        self,
        member_id: LbMemberId,
        *,
        weight: int | None = None,
        admin_state_up: bool | None = None,
    ) -> LbMember:
        member = await self._members.get(member_id)
        if member is None:
            raise NotFoundError(f"member {member_id} не найден")
        member.update(weight=weight, admin_state_up=admin_state_up, now=self._clock.now())
        await self._members.save(member)
        await self._events.publish(
            event_type="lb_member.updated",
            resource_type="lb_member",
            resource_id=member.id,
            project_id=None,
        )
        return member


class RemoveLbMember:
    def __init__(self, *, members: LbMemberRepository, events: EventPublisher) -> None:
        self._members = members
        self._events = events

    async def execute(self, member_id: LbMemberId) -> None:
        member = await self._members.get(member_id)
        if member is None:
            raise NotFoundError(f"member {member_id} не найден")
        await self._members.delete(member_id)
        await self._events.publish(
            event_type="lb_member.removed",
            resource_type="lb_member",
            resource_id=member_id,
            project_id=None,
        )


# ---------------------------------------------------------------------------
# N4-07  HealthMonitor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateHealthMonitorCommand:
    pool_id: LbPoolId
    check_type: str
    delay: int = 5
    timeout: int = 3
    max_retries: int = 3
    url_path: str = "/health"
    http_method: str = "GET"
    expected_codes: str = "200"


@dataclass(frozen=True)
class UpdateHealthMonitorCommand:
    monitor_id: HealthMonitorId
    delay: int | None = None
    timeout: int | None = None
    max_retries: int | None = None
    url_path: str | None = None
    http_method: str | None = None
    expected_codes: str | None = None


class CreateHealthMonitor:
    """Создаёт health monitor для пула LB (N4-07)."""

    def __init__(
        self,
        *,
        monitors: HealthMonitorRepository,
        pools: LbPoolRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._monitors = monitors
        self._pools = pools
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateHealthMonitorCommand) -> HealthMonitor:
        if await self._pools.get(cmd.pool_id) is None:
            raise NotFoundError(f"пул {cmd.pool_id} не найден")
        existing = await self._monitors.get_by_pool(cmd.pool_id)
        if existing is not None:
            raise ValidationError(f"пул {cmd.pool_id} уже имеет health monitor")
        now = self._clock.now()
        monitor = HealthMonitor(
            id=self._ids.health_monitor(),
            pool_id=cmd.pool_id,
            check_type=HealthCheckType(cmd.check_type),
            delay=cmd.delay,
            timeout=cmd.timeout,
            max_retries=cmd.max_retries,
            url_path=cmd.url_path,
            http_method=cmd.http_method,
            expected_codes=cmd.expected_codes,
            created_at=now,
            updated_at=now,
        )
        await self._monitors.save(monitor)
        await self._events.publish(
            event_type="health_monitor.created",
            resource_type="health_monitor",
            resource_id=monitor.id,
            project_id=None,
        )
        return monitor


class GetHealthMonitor:
    def __init__(self, *, monitors: HealthMonitorRepository) -> None:
        self._monitors = monitors

    async def execute(self, monitor_id: HealthMonitorId) -> HealthMonitor:
        monitor = await self._monitors.get(monitor_id)
        if monitor is None:
            raise NotFoundError(f"health monitor {monitor_id} не найден")
        return monitor


class UpdateHealthMonitor:
    def __init__(
        self,
        *,
        monitors: HealthMonitorRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._monitors = monitors
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateHealthMonitorCommand) -> HealthMonitor:
        monitor = await self._monitors.get(cmd.monitor_id)
        if monitor is None:
            raise NotFoundError(f"health monitor {cmd.monitor_id} не найден")
        monitor.update(
            delay=cmd.delay,
            timeout=cmd.timeout,
            max_retries=cmd.max_retries,
            url_path=cmd.url_path,
            http_method=cmd.http_method,
            expected_codes=cmd.expected_codes,
            now=self._clock.now(),
        )
        await self._monitors.save(monitor)
        await self._events.publish(
            event_type="health_monitor.updated",
            resource_type="health_monitor",
            resource_id=monitor.id,
            project_id=None,
        )
        return monitor


class DeleteHealthMonitor:
    def __init__(self, *, monitors: HealthMonitorRepository, events: EventPublisher) -> None:
        self._monitors = monitors
        self._events = events

    async def execute(self, monitor_id: HealthMonitorId) -> None:
        monitor = await self._monitors.get(monitor_id)
        if monitor is None:
            raise NotFoundError(f"health monitor {monitor_id} не найден")
        await self._monitors.delete(monitor_id)
        await self._events.publish(
            event_type="health_monitor.deleted",
            resource_type="health_monitor",
            resource_id=monitor_id,
            project_id=None,
        )
