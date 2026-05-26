"""Unit-тесты N4 — Governance & Scale.

Покрывают:
  N4-01  ProjectQuota: SetProjectQuota, GetProjectQuota, DeleteProjectQuota,
                       CheckProjectUsage, QuotaService
  N4-02  PreflightChecker: check_router (все проверки)
  N4-03  ResourceSnapshot: TakeResourceSnapshot, GetResourceSnapshot,
                           ListResourceSnapshots, DeleteResourceSnapshot
  N4-04  GatewayBond: CreateGatewayBond, GetGatewayBond, ListGatewayBonds,
                      UpdateGatewayBond, DeleteGatewayBond, ApplyGatewayBond,
                      BondConfigurator
  N4-05  RetentionPolicy: SetRetentionPolicy, GetRetentionPolicy,
                          ListRetentionPolicies, DeleteRetentionPolicy
  N4-06  LoadBalancer: CreateLoadBalancer, GetLoadBalancer, UpdateLoadBalancer,
                       DeleteLoadBalancer, ApplyLoadBalancer, SetLbAdminState,
                       CreateLbListener, CreateLbPool, AddLbMember, LbConfigurator
  N4-07  HealthMonitor: CreateHealthMonitor, GetHealthMonitor,
                        UpdateHealthMonitor, DeleteHealthMonitor
"""

from __future__ import annotations

import pytest

from sdn_controller.adapters.memory.repositories import (
    InMemoryGatewayBondRepository,
    InMemoryHealthMonitorRepository,
    InMemoryLbListenerRepository,
    InMemoryLbMemberRepository,
    InMemoryLbPoolRepository,
    InMemoryLoadBalancerRepository,
    InMemoryProjectQuotaRepository,
    InMemoryResourceSnapshotRepository,
    InMemoryRetentionPolicyRepository,
    InMemoryRouterRepository,
)
from sdn_controller.core.entities.gateway_bond import GatewayBond
from sdn_controller.core.entities.health_monitor import HealthMonitor
from sdn_controller.core.entities.load_balancer import LbListener, LbMember, LbPool, LoadBalancer
from sdn_controller.core.entities.project_quota import ProjectQuota
from sdn_controller.core.entities.resource_snapshot import ResourceSnapshot
from sdn_controller.core.entities.retention_policy import RetentionPolicy
from sdn_controller.core.services.bond_configurator import BondConfigurator
from sdn_controller.core.services.lb_configurator import LbConfigurator
from sdn_controller.core.services.preflight_checker import PreflightChecker
from sdn_controller.core.services.quota_service import QuotaService, QuotaViolation
from sdn_controller.core.use_cases.n4 import (
    AddLbMember,
    AddLbMemberCommand,
    ApplyGatewayBond,
    ApplyLoadBalancer,
    CheckProjectUsage,
    CreateGatewayBond,
    CreateGatewayBondCommand,
    CreateHealthMonitor,
    CreateHealthMonitorCommand,
    CreateLbListener,
    CreateLbListenerCommand,
    CreateLbPool,
    CreateLbPoolCommand,
    CreateLoadBalancer,
    CreateLoadBalancerCommand,
    DeleteGatewayBond,
    DeleteHealthMonitor,
    DeleteLbPool,
    DeleteProjectQuota,
    DeleteResourceSnapshot,
    DeleteRetentionPolicy,
    GetGatewayBond,
    GetHealthMonitor,
    GetLoadBalancer,
    GetProjectQuota,
    GetResourceSnapshot,
    GetRetentionPolicy,
    ListGatewayBonds,
    ListLbMembers,
    ListLbPools,
    ListLoadBalancers,
    ListResourceSnapshots,
    ListRetentionPolicies,
    RemoveLbMember,
    RunPreflightRouter,
    SetLbAdminState,
    SetProjectQuota,
    SetProjectQuotaCommand,
    SetRetentionPolicy,
    SetRetentionPolicyCommand,
    TakeResourceSnapshot,
    TakeResourceSnapshotCommand,
    UpdateGatewayBond,
    UpdateGatewayBondCommand,
    UpdateHealthMonitor,
    UpdateHealthMonitorCommand,
    UpdateLoadBalancer,
)
from sdn_controller.core.value_objects.enums import (
    BondMode,
    HealthCheckType,
    HaMode,
    LbAlgorithm,
    LbProtocol,
    LbStatus,
    QuotaResource,
    RetentionScope,
    RouterStatus,
    SessionPersistence,
)
from sdn_controller.core.value_objects.errors import NotFoundError, ValidationError
from sdn_controller.core.value_objects.ids import (
    LoadBalancerId,
    NetworkId,
    NodeId,
    ProjectId,
    RouterId,
)
from tests.conftest import FrozenClock, CountingIdFactory


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_clock() -> FrozenClock:
    return FrozenClock()


def make_ids() -> CountingIdFactory:
    return CountingIdFactory()


class _NullEvents:
    async def publish(self, **kwargs: object) -> None:  # type: ignore[override]
        pass


def null_events() -> _NullEvents:
    return _NullEvents()


# ---------------------------------------------------------------------------
# N4-01  ProjectQuota
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_project_quota_creates_new() -> None:
    quotas = InMemoryProjectQuotaRepository()
    clock = make_clock()
    ids = make_ids()
    uc = SetProjectQuota(quotas=quotas, clock=clock, ids=ids, events=null_events())
    cmd = SetProjectQuotaCommand(
        project_id=ProjectId("proj_1"),
        resource=QuotaResource.ROUTERS.value,
        limit=10,
    )
    quota = await uc.execute(cmd)
    assert quota.get_limit(QuotaResource.ROUTERS) == 10
    assert quota.project_id == "proj_1"


@pytest.mark.anyio
async def test_set_project_quota_updates_existing() -> None:
    quotas = InMemoryProjectQuotaRepository()
    clock = make_clock()
    ids = make_ids()
    uc = SetProjectQuota(quotas=quotas, clock=clock, ids=ids, events=null_events())
    pid = ProjectId("proj_1")
    cmd1 = SetProjectQuotaCommand(pid, resource=QuotaResource.ROUTERS.value, limit=5)
    cmd2 = SetProjectQuotaCommand(pid, resource=QuotaResource.ROUTERS.value, limit=20)
    await uc.execute(cmd1)
    quota = await uc.execute(cmd2)
    assert quota.get_limit(QuotaResource.ROUTERS) == 20


@pytest.mark.anyio
async def test_get_project_quota_none_if_missing() -> None:
    quotas = InMemoryProjectQuotaRepository()
    result = await GetProjectQuota(quotas=quotas).execute(ProjectId("x"))
    assert result is None


@pytest.mark.anyio
async def test_delete_project_quota() -> None:
    quotas = InMemoryProjectQuotaRepository()
    clock = make_clock()
    ids = make_ids()
    pid = ProjectId("proj_1")
    await SetProjectQuota(quotas=quotas, clock=clock, ids=ids, events=null_events()).execute(
        SetProjectQuotaCommand(pid, resource=QuotaResource.ROUTERS.value, limit=10)
    )
    await DeleteProjectQuota(quotas=quotas, events=null_events()).execute(pid)
    assert await quotas.get_by_project(pid) is None


@pytest.mark.anyio
async def test_delete_project_quota_idempotent() -> None:
    """Удаление несуществующей квоты не бросает исключение."""
    quotas = InMemoryProjectQuotaRepository()
    await DeleteProjectQuota(quotas=quotas, events=null_events()).execute(ProjectId("missing"))


@pytest.mark.anyio
async def test_check_project_usage_no_quota() -> None:
    quotas = InMemoryProjectQuotaRepository()
    routers = InMemoryRouterRepository()
    lbs = InMemoryLoadBalancerRepository()
    result = await CheckProjectUsage(
        quotas=quotas, routers=routers, load_balancers=lbs
    ).execute(ProjectId("p1"))
    assert result["violations"] == []
    assert result["usage"][QuotaResource.ROUTERS.value] == 0


def test_quota_service_no_violation() -> None:
    clock = make_clock()
    ids = make_ids()
    quota = ProjectQuota(
        id=ids.project_quota(),
        project_id=ProjectId("p1"),
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    quota.set_limit(QuotaResource.ROUTERS, 5, now=clock.now())
    svc = QuotaService()
    violations = svc.compute_violations(quota, {QuotaResource.ROUTERS.value: 3})
    assert violations == []


def test_quota_service_violation() -> None:
    clock = make_clock()
    ids = make_ids()
    quota = ProjectQuota(
        id=ids.project_quota(),
        project_id=ProjectId("p1"),
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    quota.set_limit(QuotaResource.ROUTERS, 2, now=clock.now())
    svc = QuotaService()
    violations = svc.compute_violations(quota, {QuotaResource.ROUTERS.value: 5})
    assert len(violations) == 1
    assert violations[0].resource == QuotaResource.ROUTERS
    assert violations[0].limit == 2
    assert violations[0].current == 5


# ---------------------------------------------------------------------------
# N4-02  PreflightChecker
# ---------------------------------------------------------------------------


def _make_router(*, ha_mode: str = "none", vrrp_priority: int | None = None,
                 vrrp_vrid: int | None = None, admin_state_up: bool = True,
                 external_network_id: str | None = None) -> object:
    """Создаёт минимальный Router-объект для тестов preflight."""
    from sdn_controller.core.entities.router import Router
    from datetime import datetime, UTC
    return Router(
        id=RouterId("rtr_t"),
        name="test",
        status=RouterStatus.BUILD,
        project_id=ProjectId("p1"),
        external_network_id=NetworkId(external_network_id) if external_network_id else None,
        ha_mode=HaMode(ha_mode),
        vrrp_priority=vrrp_priority,
        vrrp_vrid=vrrp_vrid,
        admin_state_up=admin_state_up,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_preflight_ok_minimal_router() -> None:
    checker = PreflightChecker()
    router = _make_router()
    issues = checker.check_router(router)  # type: ignore[arg-type]
    assert all(i.severity != "error" for i in issues)


def test_preflight_vrrp_missing_priority() -> None:
    checker = PreflightChecker()
    router = _make_router(ha_mode="vrrp", vrrp_vrid=1)
    issues = checker.check_router(router)  # type: ignore[arg-type]
    codes = {i.code for i in issues}
    assert "VRRP_NO_PRIORITY" in codes


def test_preflight_vrrp_missing_vrid() -> None:
    checker = PreflightChecker()
    router = _make_router(ha_mode="vrrp", vrrp_priority=100)
    issues = checker.check_router(router)  # type: ignore[arg-type]
    codes = {i.code for i in issues}
    assert "VRRP_NO_VRID" in codes


def test_preflight_admin_down_warning() -> None:
    checker = PreflightChecker()
    router = _make_router(admin_state_up=False)
    issues = checker.check_router(router)  # type: ignore[arg-type]
    warnings = [i for i in issues if i.code == "ADMIN_DOWN"]
    assert len(warnings) == 1
    assert warnings[0].severity == "warning"


@pytest.mark.anyio
async def test_run_preflight_router_not_found() -> None:
    routers = InMemoryRouterRepository()
    with pytest.raises(NotFoundError):
        await RunPreflightRouter(routers=routers).execute(RouterId("missing"))


@pytest.mark.anyio
async def test_run_preflight_router_error_raises() -> None:
    routers = InMemoryRouterRepository()
    router = _make_router(ha_mode="vrrp")  # без vrid и priority → ошибки
    await routers.save(router)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="preflight"):
        await RunPreflightRouter(routers=routers).execute(RouterId("rtr_t"))


@pytest.mark.anyio
async def test_run_preflight_router_returns_warnings() -> None:
    routers = InMemoryRouterRepository()
    router = _make_router(admin_state_up=False)  # только warning
    await routers.save(router)  # type: ignore[arg-type]
    issues = await RunPreflightRouter(routers=routers).execute(RouterId("rtr_t"))
    assert any(i["severity"] == "warning" for i in issues)


# ---------------------------------------------------------------------------
# N4-03  ResourceSnapshot
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_take_resource_snapshot() -> None:
    snapshots = InMemoryResourceSnapshotRepository()
    routers = InMemoryRouterRepository()
    clock = make_clock()
    ids = make_ids()
    cmd = TakeResourceSnapshotCommand(
        project_id=ProjectId("p1"),
        label="v1.0",
        include_routers=True,
    )
    snap = await TakeResourceSnapshot(
        snapshots=snapshots, routers=routers, clock=clock, ids=ids, events=null_events()
    ).execute(cmd)
    assert snap.version == 1
    assert snap.label == "v1.0"
    assert "routers" in snap.resource_types


@pytest.mark.anyio
async def test_take_resource_snapshot_version_increments() -> None:
    snapshots = InMemoryResourceSnapshotRepository()
    routers = InMemoryRouterRepository()
    clock = make_clock()
    ids = make_ids()
    uc = TakeResourceSnapshot(
        snapshots=snapshots, routers=routers, clock=clock, ids=ids, events=null_events()
    )
    s1 = await uc.execute(TakeResourceSnapshotCommand(project_id=ProjectId("p1")))
    s2 = await uc.execute(TakeResourceSnapshotCommand(project_id=ProjectId("p1")))
    assert s2.version == s1.version + 1


@pytest.mark.anyio
async def test_get_resource_snapshot_not_found() -> None:
    snapshots = InMemoryResourceSnapshotRepository()
    from sdn_controller.core.value_objects.ids import ResourceSnapshotId
    with pytest.raises(NotFoundError):
        await GetResourceSnapshot(snapshots=snapshots).execute(ResourceSnapshotId("x"))


@pytest.mark.anyio
async def test_list_resource_snapshots_by_project() -> None:
    snapshots = InMemoryResourceSnapshotRepository()
    routers = InMemoryRouterRepository()
    clock = make_clock()
    ids = make_ids()
    uc = TakeResourceSnapshot(
        snapshots=snapshots, routers=routers, clock=clock, ids=ids, events=null_events()
    )
    await uc.execute(TakeResourceSnapshotCommand(project_id=ProjectId("p1")))
    await uc.execute(TakeResourceSnapshotCommand(project_id=ProjectId("p2")))
    result = await ListResourceSnapshots(snapshots=snapshots).execute(project_id=ProjectId("p1"))
    assert len(result) == 1
    assert result[0].project_id == "p1"


@pytest.mark.anyio
async def test_delete_resource_snapshot() -> None:
    snapshots = InMemoryResourceSnapshotRepository()
    routers = InMemoryRouterRepository()
    clock = make_clock()
    ids = make_ids()
    snap = await TakeResourceSnapshot(
        snapshots=snapshots, routers=routers, clock=clock, ids=ids, events=null_events()
    ).execute(TakeResourceSnapshotCommand(project_id=ProjectId("p1")))
    await DeleteResourceSnapshot(snapshots=snapshots, events=null_events()).execute(snap.id)
    result = await ListResourceSnapshots(snapshots=snapshots).execute()
    assert result == []


# ---------------------------------------------------------------------------
# N4-04  GatewayBond + BondConfigurator
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_gateway_bond() -> None:
    bonds = InMemoryGatewayBondRepository()
    clock = make_clock()
    ids = make_ids()
    cmd = CreateGatewayBondCommand(
        name="gw-bond",
        node_id=NodeId("node_1"),
        bond_name="bond0",
        mode="lacp",
        members=["eth0", "eth1"],
        mtu=9000,
    )
    bond = await CreateGatewayBond(
        bonds=bonds, clock=clock, ids=ids, events=null_events()
    ).execute(cmd)
    assert bond.bond_name == "bond0"
    assert bond.mode == BondMode.LACP
    assert bond.mtu == 9000
    assert bond.members == ["eth0", "eth1"]


@pytest.mark.anyio
async def test_get_gateway_bond_not_found() -> None:
    bonds = InMemoryGatewayBondRepository()
    from sdn_controller.core.value_objects.ids import GatewayBondId
    with pytest.raises(NotFoundError):
        await GetGatewayBond(bonds=bonds).execute(GatewayBondId("x"))


@pytest.mark.anyio
async def test_list_gateway_bonds_by_node() -> None:
    bonds = InMemoryGatewayBondRepository()
    clock = make_clock()
    ids = make_ids()
    uc = CreateGatewayBond(bonds=bonds, clock=clock, ids=ids, events=null_events())
    await uc.execute(CreateGatewayBondCommand("b1", NodeId("n1"), "bond0"))
    await uc.execute(CreateGatewayBondCommand("b2", NodeId("n2"), "bond0"))
    result = await ListGatewayBonds(bonds=bonds).execute(node_id=NodeId("n1"))
    assert len(result) == 1
    assert result[0].node_id == "n1"


@pytest.mark.anyio
async def test_update_gateway_bond() -> None:
    bonds = InMemoryGatewayBondRepository()
    clock = make_clock()
    ids = make_ids()
    bond = await CreateGatewayBond(
        bonds=bonds, clock=clock, ids=ids, events=null_events()
    ).execute(CreateGatewayBondCommand("b1", NodeId("n1"), "bond0"))
    cmd = UpdateGatewayBondCommand(bond_id=bond.id, mtu=4000)
    updated = await UpdateGatewayBond(
        bonds=bonds, clock=clock, events=null_events()
    ).execute(cmd)
    assert updated.mtu == 4000


@pytest.mark.anyio
async def test_delete_gateway_bond() -> None:
    bonds = InMemoryGatewayBondRepository()
    clock = make_clock()
    ids = make_ids()
    bond = await CreateGatewayBond(
        bonds=bonds, clock=clock, ids=ids, events=null_events()
    ).execute(CreateGatewayBondCommand("b1", NodeId("n1"), "bond0"))
    await DeleteGatewayBond(bonds=bonds, events=null_events()).execute(bond.id)
    assert await bonds.get(bond.id) is None


@pytest.mark.anyio
async def test_apply_gateway_bond_generates_config() -> None:
    bonds = InMemoryGatewayBondRepository()
    clock = make_clock()
    ids = make_ids()
    bond = await CreateGatewayBond(
        bonds=bonds, clock=clock, ids=ids, events=null_events()
    ).execute(CreateGatewayBondCommand("b1", NodeId("n1"), "bond0", mode="lacp", members=["eth0"]))
    applied = await ApplyGatewayBond(
        bonds=bonds, clock=clock, events=null_events()
    ).execute(bond.id)
    assert applied.applied_config is not None
    assert "802.3ad" in applied.applied_config


def test_bond_configurator_active_backup() -> None:
    clock = make_clock()
    ids = make_ids()
    bond = GatewayBond(
        id=ids.gateway_bond(),
        name="test",
        node_id=NodeId("n1"),
        bond_name="bond0",
        mode=BondMode.ACTIVE_BACKUP,
        members=["eth0", "eth1"],
        mtu=1500,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    cfg = BondConfigurator().generate(bond, now=clock.now())
    assert "active-backup" in cfg
    assert "bond0" in cfg
    assert "eth0" in cfg


def test_bond_configurator_lacp() -> None:
    clock = make_clock()
    ids = make_ids()
    bond = GatewayBond(
        id=ids.gateway_bond(),
        name="test",
        node_id=NodeId("n1"),
        bond_name="bond1",
        mode=BondMode.LACP,
        members=["eth2"],
        mtu=9000,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    cfg = BondConfigurator().generate(bond, now=clock.now())
    assert "802.3ad" in cfg
    assert "9000" in cfg


def test_gateway_bond_invalid_mtu() -> None:
    clock = make_clock()
    ids = make_ids()
    with pytest.raises(ValidationError, match="mtu"):
        GatewayBond(
            id=ids.gateway_bond(),
            name="x",
            node_id=NodeId("n1"),
            bond_name="bond0",
            mtu=100,  # < 500
            created_at=clock.now(),
            updated_at=clock.now(),
        )


# ---------------------------------------------------------------------------
# N4-05  RetentionPolicy
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_retention_policy_creates() -> None:
    policies = InMemoryRetentionPolicyRepository()
    clock = make_clock()
    ids = make_ids()
    cmd = SetRetentionPolicyCommand(
        scope=RetentionScope.AUDIT_EVENTS.value,
        retention_days=90,
        description="стандарт",
    )
    policy = await SetRetentionPolicy(
        policies=policies, clock=clock, ids=ids, events=null_events()
    ).execute(cmd)
    assert policy.retention_days == 90
    assert policy.scope == RetentionScope.AUDIT_EVENTS


@pytest.mark.anyio
async def test_set_retention_policy_updates_existing() -> None:
    policies = InMemoryRetentionPolicyRepository()
    clock = make_clock()
    ids = make_ids()
    uc = SetRetentionPolicy(policies=policies, clock=clock, ids=ids, events=null_events())
    cmd1 = SetRetentionPolicyCommand(scope=RetentionScope.AUDIT_EVENTS.value, retention_days=30)
    cmd2 = SetRetentionPolicyCommand(scope=RetentionScope.AUDIT_EVENTS.value, retention_days=180)
    await uc.execute(cmd1)
    policy = await uc.execute(cmd2)
    assert policy.retention_days == 180


@pytest.mark.anyio
async def test_list_retention_policies() -> None:
    policies = InMemoryRetentionPolicyRepository()
    clock = make_clock()
    ids = make_ids()
    uc = SetRetentionPolicy(policies=policies, clock=clock, ids=ids, events=null_events())
    await uc.execute(SetRetentionPolicyCommand(scope=RetentionScope.AUDIT_EVENTS.value, retention_days=30))
    await uc.execute(SetRetentionPolicyCommand(scope=RetentionScope.SNAPSHOTS.value, retention_days=7))
    result = await ListRetentionPolicies(policies=policies).execute()
    assert len(result) == 2


@pytest.mark.anyio
async def test_delete_retention_policy() -> None:
    policies = InMemoryRetentionPolicyRepository()
    clock = make_clock()
    ids = make_ids()
    policy = await SetRetentionPolicy(
        policies=policies, clock=clock, ids=ids, events=null_events()
    ).execute(SetRetentionPolicyCommand(scope=RetentionScope.AUDIT_EVENTS.value, retention_days=30))
    await DeleteRetentionPolicy(policies=policies, events=null_events()).execute(policy.id)
    result = await ListRetentionPolicies(policies=policies).execute()
    assert result == []


def test_retention_policy_invalid_days() -> None:
    from datetime import datetime, UTC
    with pytest.raises(ValidationError, match="retention_days"):
        RetentionPolicy(
            id="ret_1",  # type: ignore[arg-type]
            scope=RetentionScope.AUDIT_EVENTS,
            retention_days=-1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


def test_retention_policy_zero_is_forever() -> None:
    """retention_days=0 означает «хранить вечно» и не вызывает исключение."""
    from datetime import datetime, UTC
    p = RetentionPolicy(
        id="ret_1",  # type: ignore[arg-type]
        scope=RetentionScope.AUDIT_EVENTS,
        retention_days=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert p.retention_days == 0


# ---------------------------------------------------------------------------
# N4-06  LoadBalancer + LbConfigurator
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_load_balancer() -> None:
    lbs = InMemoryLoadBalancerRepository()
    clock = make_clock()
    ids = make_ids()
    cmd = CreateLoadBalancerCommand(
        name="lb-test",
        vip_address="10.0.0.1",
        vip_network_id=NetworkId("net_1"),
    )
    lb = await CreateLoadBalancer(
        load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(cmd)
    assert lb.name == "lb-test"
    assert lb.vip_address == "10.0.0.1"
    assert lb.status == LbStatus.BUILD


@pytest.mark.anyio
async def test_create_load_balancer_invalid_ip() -> None:
    lbs = InMemoryLoadBalancerRepository()
    clock = make_clock()
    ids = make_ids()
    cmd = CreateLoadBalancerCommand(
        name="lb-bad",
        vip_address="not_an_ip",
        vip_network_id=NetworkId("net_1"),
    )
    with pytest.raises(ValidationError):
        await CreateLoadBalancer(
            load_balancers=lbs, clock=clock, ids=ids, events=null_events()
        ).execute(cmd)


@pytest.mark.anyio
async def test_get_load_balancer_not_found() -> None:
    lbs = InMemoryLoadBalancerRepository()
    with pytest.raises(NotFoundError):
        await GetLoadBalancer(load_balancers=lbs).execute(LoadBalancerId("x"))


@pytest.mark.anyio
async def test_list_load_balancers_by_project() -> None:
    lbs = InMemoryLoadBalancerRepository()
    clock = make_clock()
    ids = make_ids()
    uc = CreateLoadBalancer(load_balancers=lbs, clock=clock, ids=ids, events=null_events())
    await uc.execute(CreateLoadBalancerCommand("lb1", "10.0.0.1", NetworkId("n1"), project_id=ProjectId("p1")))
    await uc.execute(CreateLoadBalancerCommand("lb2", "10.0.0.2", NetworkId("n1"), project_id=ProjectId("p2")))
    result = await ListLoadBalancers(load_balancers=lbs).execute(project_id=ProjectId("p1"))
    assert len(result) == 1


@pytest.mark.anyio
async def test_update_load_balancer() -> None:
    lbs = InMemoryLoadBalancerRepository()
    clock = make_clock()
    ids = make_ids()
    lb = await CreateLoadBalancer(
        load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLoadBalancerCommand("lb1", "10.0.0.1", NetworkId("n1")))
    updated = await UpdateLoadBalancer(
        load_balancers=lbs, clock=clock, events=null_events()
    ).execute(lb.id, name="lb-renamed")
    assert updated.name == "lb-renamed"


@pytest.mark.anyio
async def test_set_lb_admin_state() -> None:
    lbs = InMemoryLoadBalancerRepository()
    clock = make_clock()
    ids = make_ids()
    lb = await CreateLoadBalancer(
        load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLoadBalancerCommand("lb1", "10.0.0.1", NetworkId("n1")))
    result = await SetLbAdminState(
        load_balancers=lbs, clock=clock, events=null_events()
    ).execute(lb.id, up=False)
    assert result.admin_state_up is False
    assert result.status == LbStatus.DOWN


@pytest.mark.anyio
async def test_apply_load_balancer_generates_config() -> None:
    lbs = InMemoryLoadBalancerRepository()
    listeners = InMemoryLbListenerRepository()
    pools = InMemoryLbPoolRepository()
    members = InMemoryLbMemberRepository()
    monitors = InMemoryHealthMonitorRepository()
    clock = make_clock()
    ids = make_ids()

    lb = await CreateLoadBalancer(
        load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLoadBalancerCommand("lb1", "10.0.0.1", NetworkId("n1")))

    pool = await CreateLbPool(
        pools=pools, load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLbPoolCommand("pool1", lb.id, "http"))

    await AddLbMember(
        members=members, pools=pools, clock=clock, ids=ids, events=null_events()
    ).execute(AddLbMemberCommand(pool.id, "192.168.1.10", 8080))

    await CreateLbListener(
        listeners=listeners, load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLbListenerCommand("l1", lb.id, "http", 80, default_pool_id=pool.id))

    applied = await ApplyLoadBalancer(
        load_balancers=lbs,
        listeners=listeners,
        pools=pools,
        members=members,
        monitors=monitors,
        clock=clock,
        events=null_events(),
    ).execute(lb.id)
    assert applied.status == LbStatus.ACTIVE
    assert applied.applied_config is not None
    assert "192.168.1.10" in applied.applied_config


def test_lb_configurator_generates_haproxy_cfg() -> None:
    clock = make_clock()
    ids = make_ids()
    lb = LoadBalancer(
        id=ids.load_balancer(),
        name="test-lb",
        vip_address="10.0.0.1",
        vip_network_id=NetworkId("n1"),
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    pool_id = ids.lb_pool()
    listener = LbListener(
        id=ids.lb_listener(),
        name="l1",
        lb_id=lb.id,
        protocol=LbProtocol.HTTP,
        protocol_port=80,
        default_pool_id=pool_id,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    pool = LbPool(
        id=pool_id,
        name="p1",
        lb_id=lb.id,
        protocol=LbProtocol.HTTP,
        lb_algorithm=LbAlgorithm.ROUND_ROBIN,
        session_persistence=SessionPersistence.NONE,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    member = LbMember(
        id=ids.lb_member(),
        pool_id=pool_id,
        address="10.0.1.5",
        protocol_port=8080,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    cfg = LbConfigurator().generate(
        lb, [listener], [pool], {pool_id: [member]}, {}, now=clock.now()
    )
    assert "haproxy.cfg" in cfg
    assert "10.0.0.1:80" in cfg
    assert "10.0.1.5:8080" in cfg
    assert "roundrobin" in cfg


# ---------------------------------------------------------------------------
# N4-07  HealthMonitor
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_health_monitor() -> None:
    monitors = InMemoryHealthMonitorRepository()
    pools = InMemoryLbPoolRepository()
    lbs = InMemoryLoadBalancerRepository()
    clock = make_clock()
    ids = make_ids()

    lb = await CreateLoadBalancer(
        load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLoadBalancerCommand("lb1", "10.0.0.1", NetworkId("n1")))
    pool = await CreateLbPool(
        pools=pools, load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLbPoolCommand("pool1", lb.id, "http"))

    cmd = CreateHealthMonitorCommand(
        pool_id=pool.id,
        check_type=HealthCheckType.HTTP.value,
        delay=10,
        timeout=5,
        max_retries=3,
        url_path="/ping",
    )
    monitor = await CreateHealthMonitor(
        monitors=monitors, pools=pools, clock=clock, ids=ids, events=null_events()
    ).execute(cmd)
    assert monitor.pool_id == pool.id
    assert monitor.check_type == HealthCheckType.HTTP
    assert monitor.url_path == "/ping"


@pytest.mark.anyio
async def test_create_health_monitor_duplicate_raises() -> None:
    monitors = InMemoryHealthMonitorRepository()
    pools = InMemoryLbPoolRepository()
    lbs = InMemoryLoadBalancerRepository()
    clock = make_clock()
    ids = make_ids()

    lb = await CreateLoadBalancer(
        load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLoadBalancerCommand("lb1", "10.0.0.1", NetworkId("n1")))
    pool = await CreateLbPool(
        pools=pools, load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLbPoolCommand("pool1", lb.id, "http"))

    cmd = CreateHealthMonitorCommand(pool_id=pool.id, check_type=HealthCheckType.HTTP.value)
    uc = CreateHealthMonitor(monitors=monitors, pools=pools, clock=clock, ids=ids, events=null_events())
    await uc.execute(cmd)
    with pytest.raises(ValidationError, match="уже имеет"):
        await uc.execute(cmd)


@pytest.mark.anyio
async def test_update_health_monitor() -> None:
    monitors = InMemoryHealthMonitorRepository()
    pools = InMemoryLbPoolRepository()
    lbs = InMemoryLoadBalancerRepository()
    clock = make_clock()
    ids = make_ids()

    lb = await CreateLoadBalancer(
        load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLoadBalancerCommand("lb1", "10.0.0.1", NetworkId("n1")))
    pool = await CreateLbPool(
        pools=pools, load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLbPoolCommand("pool1", lb.id, "http"))
    monitor = await CreateHealthMonitor(
        monitors=monitors, pools=pools, clock=clock, ids=ids, events=null_events()
    ).execute(CreateHealthMonitorCommand(pool_id=pool.id, check_type=HealthCheckType.HTTP.value))

    updated = await UpdateHealthMonitor(
        monitors=monitors, clock=clock, events=null_events()
    ).execute(UpdateHealthMonitorCommand(monitor_id=monitor.id, delay=20, url_path="/status"))
    assert updated.delay == 20
    assert updated.url_path == "/status"


@pytest.mark.anyio
async def test_delete_health_monitor() -> None:
    monitors = InMemoryHealthMonitorRepository()
    pools = InMemoryLbPoolRepository()
    lbs = InMemoryLoadBalancerRepository()
    clock = make_clock()
    ids = make_ids()

    lb = await CreateLoadBalancer(
        load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLoadBalancerCommand("lb1", "10.0.0.1", NetworkId("n1")))
    pool = await CreateLbPool(
        pools=pools, load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLbPoolCommand("pool1", lb.id, "http"))
    monitor = await CreateHealthMonitor(
        monitors=monitors, pools=pools, clock=clock, ids=ids, events=null_events()
    ).execute(CreateHealthMonitorCommand(pool_id=pool.id, check_type=HealthCheckType.TCP.value))

    from sdn_controller.core.value_objects.ids import HealthMonitorId
    await DeleteHealthMonitor(monitors=monitors, events=null_events()).execute(monitor.id)
    assert await monitors.get(monitor.id) is None


def test_health_monitor_invalid_delay() -> None:
    from datetime import datetime, UTC
    with pytest.raises(ValidationError, match="delay"):
        HealthMonitor(
            id="hm_1",  # type: ignore[arg-type]
            pool_id="lbpool_1",  # type: ignore[arg-type]
            check_type=HealthCheckType.HTTP,
            delay=0,  # < 1
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


def test_health_monitor_invalid_method() -> None:
    from datetime import datetime, UTC
    with pytest.raises(ValidationError, match="http_method"):
        HealthMonitor(
            id="hm_1",  # type: ignore[arg-type]
            pool_id="lbpool_1",  # type: ignore[arg-type]
            check_type=HealthCheckType.HTTP,
            http_method="PATCH",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


@pytest.mark.anyio
async def test_add_and_remove_lb_member() -> None:
    members = InMemoryLbMemberRepository()
    pools = InMemoryLbPoolRepository()
    lbs = InMemoryLoadBalancerRepository()
    clock = make_clock()
    ids = make_ids()

    lb = await CreateLoadBalancer(
        load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLoadBalancerCommand("lb1", "10.0.0.1", NetworkId("n1")))
    pool = await CreateLbPool(
        pools=pools, load_balancers=lbs, clock=clock, ids=ids, events=null_events()
    ).execute(CreateLbPoolCommand("pool1", lb.id, "http"))

    member = await AddLbMember(
        members=members, pools=pools, clock=clock, ids=ids, events=null_events()
    ).execute(AddLbMemberCommand(pool.id, "10.1.1.1", 8080, weight=5))
    assert member.weight == 5

    result = await ListLbMembers(members=members).execute(pool_id=pool.id)
    assert len(result) == 1

    await RemoveLbMember(members=members, events=null_events()).execute(member.id)
    result = await ListLbMembers(members=members).execute(pool_id=pool.id)
    assert result == []
