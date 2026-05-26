"""N4 REST-роутеры — Governance & Scale.

Маршруты:
  /quotas/{project_id}                    — get/set/delete квот проекта (N4-01)
  /quotas/{project_id}/usage              — проверка использования (N4-01)
  /preflight/router/{id}                  — preflight-проверка маршрутизатора (N4-02)
  /snapshots                              — list/create снапшотов (N4-03)
  /snapshots/{id}                         — get/delete снапшота (N4-03)
  /gateway-bonds                          — CRUD bond-интерфейсов (N4-04)
  /gateway-bonds/{id}/apply               — генерация netplan-конфига (N4-04)
  /retention-policies                     — CRUD политик хранения (N4-05)
  /load-balancers                         — CRUD балансировщиков (N4-06)
  /load-balancers/{id}/apply              — генерация haproxy.cfg (N4-06)
  /load-balancers/{id}/admin-state        — включение/выключение (N4-06)
  /lb-listeners                           — CRUD listener'ов (N4-06)
  /lb-pools                               — CRUD пулов (N4-06)
  /lb-members                             — add/list/update/remove участников (N4-06)
  /health-monitors                        — CRUD health monitor'ов (N4-07)

Права:
  NETWORK_READ  — список / детали
  NETWORK_WRITE — создание / изменение / удаление
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from sdn_controller.adapters.http_api.auth import require as require_permission
from sdn_controller.app.container import Container
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
    DeleteLbListener,
    DeleteLbPool,
    DeleteLoadBalancer,
    DeleteProjectQuota,
    DeleteResourceSnapshot,
    DeleteRetentionPolicy,
    GetGatewayBond,
    GetHealthMonitor,
    GetLbListener,
    GetLbMember,
    GetLbPool,
    GetLoadBalancer,
    GetProjectQuota,
    GetResourceSnapshot,
    GetRetentionPolicy,
    ListGatewayBonds,
    ListLbListeners,
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
    UpdateLbListener,
    UpdateLbMember,
    UpdateLbPool,
    UpdateLbPoolCommand,
    UpdateLoadBalancer,
)
from sdn_controller.core.value_objects.ids import (
    GatewayBondId,
    HealthMonitorId,
    LbListenerId,
    LbMemberId,
    LbPoolId,
    LoadBalancerId,
    NetworkId,
    NodeId,
    ProjectId,
    ResourceSnapshotId,
    RetentionPolicyId,
    RouterId,
)
from sdn_controller.core.value_objects.security import Permission


def _container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Pydantic-схемы — ProjectQuota (N4-01)
# ---------------------------------------------------------------------------


class QuotaSetRequest(BaseModel):
    resource: str
    limit: int | None = None


class QuotaOut(BaseModel):
    id: str
    project_id: str
    limits: dict[str, int | None]


# ---------------------------------------------------------------------------
# Pydantic-схемы — ResourceSnapshot (N4-03)
# ---------------------------------------------------------------------------


class SnapshotCreateRequest(BaseModel):
    project_id: str
    label: str = ""
    include_routers: bool = True
    include_networks: bool = True
    include_floating_ips: bool = True


class SnapshotOut(BaseModel):
    id: str
    project_id: str
    version: int
    label: str
    resource_types: list[str]
    resource_count: int
    created_at: str


# ---------------------------------------------------------------------------
# Pydantic-схемы — GatewayBond (N4-04)
# ---------------------------------------------------------------------------


class GatewayBondCreateRequest(BaseModel):
    name: str
    node_id: str
    bond_name: str
    mode: str = "none"
    members: list[str] = Field(default_factory=list)
    mtu: int = 1500
    project_id: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class GatewayBondUpdateRequest(BaseModel):
    name: str | None = None
    mode: str | None = None
    members: list[str] | None = None
    mtu: int | None = None
    labels: dict[str, str] | None = None


class GatewayBondOut(BaseModel):
    id: str
    name: str
    node_id: str
    bond_name: str
    mode: str
    members: list[str]
    mtu: int
    project_id: str | None
    applied_config: str | None
    applied_at: str | None


# ---------------------------------------------------------------------------
# Pydantic-схемы — RetentionPolicy (N4-05)
# ---------------------------------------------------------------------------


class RetentionPolicySetRequest(BaseModel):
    scope: str
    retention_days: int
    project_id: str | None = None
    description: str = ""


class RetentionPolicyOut(BaseModel):
    id: str
    scope: str
    retention_days: int
    project_id: str | None
    description: str


# ---------------------------------------------------------------------------
# Pydantic-схемы — LoadBalancer (N4-06)
# ---------------------------------------------------------------------------


class LoadBalancerCreateRequest(BaseModel):
    name: str
    vip_address: str
    vip_network_id: str
    project_id: str | None = None
    router_id: str | None = None
    description: str = ""
    provider: str = "haproxy"
    labels: dict[str, str] = Field(default_factory=dict)


class LoadBalancerUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


class LoadBalancerOut(BaseModel):
    id: str
    name: str
    vip_address: str
    vip_network_id: str
    project_id: str | None
    router_id: str | None
    description: str
    provider: str
    status: str
    admin_state_up: bool
    applied_config: str | None


class LbListenerCreateRequest(BaseModel):
    name: str
    lb_id: str
    protocol: str
    protocol_port: int
    default_pool_id: str | None = None
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class LbListenerUpdateRequest(BaseModel):
    name: str | None = None
    default_pool_id: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


class LbListenerOut(BaseModel):
    id: str
    name: str
    lb_id: str
    protocol: str
    protocol_port: int
    default_pool_id: str | None
    description: str


class LbPoolCreateRequest(BaseModel):
    name: str
    lb_id: str
    protocol: str
    lb_algorithm: str = "round_robin"
    session_persistence: str = "none"
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class LbPoolUpdateRequest(BaseModel):
    name: str | None = None
    lb_algorithm: str | None = None
    session_persistence: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


class LbPoolOut(BaseModel):
    id: str
    name: str
    lb_id: str
    protocol: str
    lb_algorithm: str
    session_persistence: str
    description: str


class LbMemberAddRequest(BaseModel):
    pool_id: str
    address: str
    protocol_port: int
    weight: int = 1
    admin_state_up: bool = True


class LbMemberUpdateRequest(BaseModel):
    weight: int | None = None
    admin_state_up: bool | None = None


class LbMemberOut(BaseModel):
    id: str
    pool_id: str
    address: str
    protocol_port: int
    weight: int
    admin_state_up: bool


# ---------------------------------------------------------------------------
# Pydantic-схемы — HealthMonitor (N4-07)
# ---------------------------------------------------------------------------


class HealthMonitorCreateRequest(BaseModel):
    pool_id: str
    check_type: str
    delay: int = 5
    timeout: int = 3
    max_retries: int = 3
    url_path: str = "/health"
    http_method: str = "GET"
    expected_codes: str = "200"


class HealthMonitorUpdateRequest(BaseModel):
    delay: int | None = None
    timeout: int | None = None
    max_retries: int | None = None
    url_path: str | None = None
    http_method: str | None = None
    expected_codes: str | None = None


class HealthMonitorOut(BaseModel):
    id: str
    pool_id: str
    check_type: str
    delay: int
    timeout: int
    max_retries: int
    url_path: str
    http_method: str
    expected_codes: str


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bond_out(bond: Any) -> dict[str, Any]:
    return {
        "id": bond.id,
        "name": bond.name,
        "node_id": bond.node_id,
        "bond_name": bond.bond_name,
        "mode": bond.mode.value,
        "members": list(bond.members),
        "mtu": bond.mtu,
        "project_id": bond.project_id,
        "applied_config": bond.applied_config,
        "applied_at": bond.applied_at.isoformat() if bond.applied_at else None,
    }


def _lb_out(lb: Any) -> dict[str, Any]:
    return {
        "id": lb.id,
        "name": lb.name,
        "vip_address": lb.vip_address,
        "vip_network_id": lb.vip_network_id,
        "project_id": lb.project_id,
        "router_id": lb.router_id,
        "description": lb.description,
        "provider": lb.provider,
        "status": lb.status.value,
        "admin_state_up": lb.admin_state_up,
        "applied_config": lb.applied_config,
    }


def _listener_out(l: Any) -> dict[str, Any]:
    return {
        "id": l.id,
        "name": l.name,
        "lb_id": l.lb_id,
        "protocol": l.protocol.value,
        "protocol_port": l.protocol_port,
        "default_pool_id": l.default_pool_id,
        "description": l.description,
    }


def _pool_out(pool: Any) -> dict[str, Any]:
    return {
        "id": pool.id,
        "name": pool.name,
        "lb_id": pool.lb_id,
        "protocol": pool.protocol.value,
        "lb_algorithm": pool.lb_algorithm.value,
        "session_persistence": pool.session_persistence.value,
        "description": pool.description,
    }


def _member_out(m: Any) -> dict[str, Any]:
    return {
        "id": m.id,
        "pool_id": m.pool_id,
        "address": m.address,
        "protocol_port": m.protocol_port,
        "weight": m.weight,
        "admin_state_up": m.admin_state_up,
    }


def _monitor_out(hm: Any) -> dict[str, Any]:
    return {
        "id": hm.id,
        "pool_id": hm.pool_id,
        "check_type": hm.check_type.value,
        "delay": hm.delay,
        "timeout": hm.timeout,
        "max_retries": hm.max_retries,
        "url_path": hm.url_path,
        "http_method": hm.http_method,
        "expected_codes": hm.expected_codes,
    }


# ---------------------------------------------------------------------------
# N4-01  ProjectQuota
# ---------------------------------------------------------------------------

quotas_router = APIRouter(prefix="/quotas", tags=["quotas"])


@quotas_router.get("/{project_id}")
async def get_quota(
    project_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    quota = await GetProjectQuota(quotas=c.quotas_repo).execute(ProjectId(project_id))
    if quota is None:
        return {"project_id": project_id, "limits": {}}
    return {"id": quota.id, "project_id": quota.project_id, "limits": quota.limits}


@quotas_router.put("/{project_id}", status_code=status.HTTP_200_OK)
async def set_quota(
    project_id: str,
    body: QuotaSetRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = SetProjectQuotaCommand(
        project_id=ProjectId(project_id),
        resource=body.resource,
        limit=body.limit,
    )
    quota = await SetProjectQuota(
        quotas=c.quotas_repo,
        clock=c.clock,
        ids=c.ids,
        events=c.events,
    ).execute(cmd)
    return {"id": quota.id, "project_id": quota.project_id, "limits": quota.limits}


@quotas_router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quota(
    project_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c: Container = _container(request)
    await DeleteProjectQuota(quotas=c.quotas_repo, events=c.events).execute(
        ProjectId(project_id)
    )


@quotas_router.get("/{project_id}/usage")
async def check_usage(
    project_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    return await CheckProjectUsage(
        quotas=c.quotas_repo,
        routers=c.routers_repo,
        load_balancers=c.load_balancers_repo,
    ).execute(ProjectId(project_id))


# ---------------------------------------------------------------------------
# N4-02  Preflight checks
# ---------------------------------------------------------------------------

preflight_router = APIRouter(prefix="/preflight", tags=["preflight"])


@preflight_router.post("/router/{router_id}")
async def preflight_router_handler(
    router_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    issues = await RunPreflightRouter(routers=c.routers_repo).execute(RouterId(router_id))
    return {"router_id": router_id, "issues": issues}


# ---------------------------------------------------------------------------
# N4-03  ResourceSnapshot
# ---------------------------------------------------------------------------

snapshots_router = APIRouter(prefix="/snapshots", tags=["snapshots"])


@snapshots_router.get("")
async def list_snapshots(
    project_id: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> list[dict[str, Any]]:
    c: Container = _container(request)
    snaps = await ListResourceSnapshots(snapshots=c.snapshots_repo).execute(
        project_id=ProjectId(project_id) if project_id else None
    )
    return [
        {
            "id": s.id,
            "project_id": s.project_id,
            "version": s.version,
            "label": s.label,
            "resource_types": s.resource_types,
            "resource_count": s.resource_count(),
            "created_at": s.created_at.isoformat(),
        }
        for s in snaps
    ]


@snapshots_router.post("", status_code=status.HTTP_201_CREATED)
async def create_snapshot(
    body: SnapshotCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = TakeResourceSnapshotCommand(
        project_id=ProjectId(body.project_id),
        label=body.label,
        include_routers=body.include_routers,
        include_networks=body.include_networks,
        include_floating_ips=body.include_floating_ips,
    )
    snap = await TakeResourceSnapshot(
        snapshots=c.snapshots_repo,
        routers=c.routers_repo,
        clock=c.clock,
        ids=c.ids,
        events=c.events,
    ).execute(cmd)
    return {
        "id": snap.id,
        "project_id": snap.project_id,
        "version": snap.version,
        "label": snap.label,
        "resource_types": snap.resource_types,
        "resource_count": snap.resource_count(),
        "created_at": snap.created_at.isoformat(),
    }


@snapshots_router.get("/{snap_id}")
async def get_snapshot(
    snap_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    snap = await GetResourceSnapshot(snapshots=c.snapshots_repo).execute(
        ResourceSnapshotId(snap_id)
    )
    return {
        "id": snap.id,
        "project_id": snap.project_id,
        "version": snap.version,
        "label": snap.label,
        "resource_types": snap.resource_types,
        "payload": snap.payload,
        "resource_count": snap.resource_count(),
        "created_at": snap.created_at.isoformat(),
    }


@snapshots_router.delete("/{snap_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_snapshot(
    snap_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c: Container = _container(request)
    await DeleteResourceSnapshot(snapshots=c.snapshots_repo, events=c.events).execute(
        ResourceSnapshotId(snap_id)
    )


# ---------------------------------------------------------------------------
# N4-04  GatewayBond
# ---------------------------------------------------------------------------

bonds_router = APIRouter(prefix="/gateway-bonds", tags=["gateway-bonds"])


@bonds_router.post("", status_code=status.HTTP_201_CREATED)
async def create_bond(
    body: GatewayBondCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = CreateGatewayBondCommand(
        name=body.name,
        node_id=NodeId(body.node_id),
        bond_name=body.bond_name,
        mode=body.mode,
        members=body.members,
        mtu=body.mtu,
        project_id=ProjectId(body.project_id) if body.project_id else None,
        labels=body.labels,
    )
    bond = await CreateGatewayBond(
        bonds=c.bonds_repo,
        clock=c.clock,
        ids=c.ids,
        events=c.events,
    ).execute(cmd)
    return _bond_out(bond)


@bonds_router.get("")
async def list_bonds(
    node_id: str | None = None,
    project_id: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> list[dict[str, Any]]:
    c: Container = _container(request)
    bonds = await ListGatewayBonds(bonds=c.bonds_repo).execute(
        node_id=NodeId(node_id) if node_id else None,
        project_id=ProjectId(project_id) if project_id else None,
    )
    return [_bond_out(b) for b in bonds]


@bonds_router.get("/{bond_id}")
async def get_bond(
    bond_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    bond = await GetGatewayBond(bonds=c.bonds_repo).execute(GatewayBondId(bond_id))
    return _bond_out(bond)


@bonds_router.patch("/{bond_id}")
async def update_bond(
    bond_id: str,
    body: GatewayBondUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = UpdateGatewayBondCommand(
        bond_id=GatewayBondId(bond_id),
        name=body.name,
        mode=body.mode,
        members=body.members,
        mtu=body.mtu,
        labels=body.labels,
    )
    bond = await UpdateGatewayBond(
        bonds=c.bonds_repo, clock=c.clock, events=c.events
    ).execute(cmd)
    return _bond_out(bond)


@bonds_router.delete("/{bond_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bond(
    bond_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c: Container = _container(request)
    await DeleteGatewayBond(bonds=c.bonds_repo, events=c.events).execute(
        GatewayBondId(bond_id)
    )


@bonds_router.post("/{bond_id}/apply")
async def apply_bond(
    bond_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    bond = await ApplyGatewayBond(
        bonds=c.bonds_repo, clock=c.clock, events=c.events
    ).execute(GatewayBondId(bond_id))
    return _bond_out(bond)


# ---------------------------------------------------------------------------
# N4-05  RetentionPolicy
# ---------------------------------------------------------------------------

retention_router = APIRouter(prefix="/retention-policies", tags=["retention-policies"])


@retention_router.post("", status_code=status.HTTP_201_CREATED)
async def set_retention_policy(
    body: RetentionPolicySetRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = SetRetentionPolicyCommand(
        scope=body.scope,
        retention_days=body.retention_days,
        project_id=ProjectId(body.project_id) if body.project_id else None,
        description=body.description,
    )
    policy = await SetRetentionPolicy(
        policies=c.retention_policies_repo,
        clock=c.clock,
        ids=c.ids,
        events=c.events,
    ).execute(cmd)
    return {
        "id": policy.id,
        "scope": policy.scope.value,
        "retention_days": policy.retention_days,
        "project_id": policy.project_id,
        "description": policy.description,
    }


@retention_router.get("")
async def list_retention_policies(
    project_id: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> list[dict[str, Any]]:
    c: Container = _container(request)
    policies = await ListRetentionPolicies(policies=c.retention_policies_repo).execute(
        project_id=ProjectId(project_id) if project_id else None
    )
    return [
        {
            "id": p.id,
            "scope": p.scope.value,
            "retention_days": p.retention_days,
            "project_id": p.project_id,
            "description": p.description,
        }
        for p in policies
    ]


@retention_router.get("/{policy_id}")
async def get_retention_policy(
    policy_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    policy = await GetRetentionPolicy(policies=c.retention_policies_repo).execute(
        RetentionPolicyId(policy_id)
    )
    return {
        "id": policy.id,
        "scope": policy.scope.value,
        "retention_days": policy.retention_days,
        "project_id": policy.project_id,
        "description": policy.description,
    }


@retention_router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_retention_policy(
    policy_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c: Container = _container(request)
    await DeleteRetentionPolicy(
        policies=c.retention_policies_repo, events=c.events
    ).execute(RetentionPolicyId(policy_id))


# ---------------------------------------------------------------------------
# N4-06  LoadBalancer
# ---------------------------------------------------------------------------

lbs_router = APIRouter(prefix="/load-balancers", tags=["load-balancers"])


@lbs_router.post("", status_code=status.HTTP_201_CREATED)
async def create_lb(
    body: LoadBalancerCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = CreateLoadBalancerCommand(
        name=body.name,
        vip_address=body.vip_address,
        vip_network_id=NetworkId(body.vip_network_id),
        project_id=ProjectId(body.project_id) if body.project_id else None,
        router_id=None,
        description=body.description,
        provider=body.provider,
        labels=body.labels,
    )
    lb = await CreateLoadBalancer(
        load_balancers=c.load_balancers_repo,
        clock=c.clock,
        ids=c.ids,
        events=c.events,
    ).execute(cmd)
    return _lb_out(lb)


@lbs_router.get("")
async def list_lbs(
    project_id: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> list[dict[str, Any]]:
    c: Container = _container(request)
    lbs = await ListLoadBalancers(load_balancers=c.load_balancers_repo).execute(
        project_id=ProjectId(project_id) if project_id else None
    )
    return [_lb_out(lb) for lb in lbs]


@lbs_router.get("/{lb_id}")
async def get_lb(
    lb_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    lb = await GetLoadBalancer(load_balancers=c.load_balancers_repo).execute(
        LoadBalancerId(lb_id)
    )
    return _lb_out(lb)


@lbs_router.patch("/{lb_id}")
async def update_lb(
    lb_id: str,
    body: LoadBalancerUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    lb = await UpdateLoadBalancer(
        load_balancers=c.load_balancers_repo, clock=c.clock, events=c.events
    ).execute(
        LoadBalancerId(lb_id),
        name=body.name,
        description=body.description,
        labels=body.labels,
    )
    return _lb_out(lb)


@lbs_router.delete("/{lb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lb(
    lb_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c: Container = _container(request)
    await DeleteLoadBalancer(
        load_balancers=c.load_balancers_repo, events=c.events
    ).execute(LoadBalancerId(lb_id))


@lbs_router.post("/{lb_id}/apply")
async def apply_lb(
    lb_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    lb = await ApplyLoadBalancer(
        load_balancers=c.load_balancers_repo,
        listeners=c.lb_listeners_repo,
        pools=c.lb_pools_repo,
        members=c.lb_members_repo,
        monitors=c.health_monitors_repo,
        clock=c.clock,
        events=c.events,
    ).execute(LoadBalancerId(lb_id))
    return _lb_out(lb)


@lbs_router.put("/{lb_id}/admin-state")
async def set_lb_admin_state(
    lb_id: str,
    request: Request,
    up: bool = True,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    lb = await SetLbAdminState(
        load_balancers=c.load_balancers_repo, clock=c.clock, events=c.events
    ).execute(LoadBalancerId(lb_id), up=up)
    return _lb_out(lb)


# ---------------------------------------------------------------------------
# N4-06  LbListener
# ---------------------------------------------------------------------------

listeners_router = APIRouter(prefix="/lb-listeners", tags=["lb-listeners"])


@listeners_router.post("", status_code=status.HTTP_201_CREATED)
async def create_listener(
    body: LbListenerCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = CreateLbListenerCommand(
        name=body.name,
        lb_id=LoadBalancerId(body.lb_id),
        protocol=body.protocol,
        protocol_port=body.protocol_port,
        default_pool_id=LbPoolId(body.default_pool_id) if body.default_pool_id else None,
        description=body.description,
        labels=body.labels,
    )
    listener = await CreateLbListener(
        listeners=c.lb_listeners_repo,
        load_balancers=c.load_balancers_repo,
        clock=c.clock,
        ids=c.ids,
        events=c.events,
    ).execute(cmd)
    return _listener_out(listener)


@listeners_router.get("")
async def list_listeners(
    lb_id: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> list[dict[str, Any]]:
    c: Container = _container(request)
    items = await ListLbListeners(listeners=c.lb_listeners_repo).execute(
        lb_id=LoadBalancerId(lb_id) if lb_id else None
    )
    return [_listener_out(l) for l in items]


@listeners_router.get("/{listener_id}")
async def get_listener(
    listener_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    l = await GetLbListener(listeners=c.lb_listeners_repo).execute(
        LbListenerId(listener_id)
    )
    return _listener_out(l)


@listeners_router.patch("/{listener_id}")
async def update_listener(
    listener_id: str,
    body: LbListenerUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    l = await UpdateLbListener(
        listeners=c.lb_listeners_repo, clock=c.clock, events=c.events
    ).execute(
        LbListenerId(listener_id),
        name=body.name,
        default_pool_id=LbPoolId(body.default_pool_id) if body.default_pool_id else None,
        description=body.description,
        labels=body.labels,
    )
    return _listener_out(l)


@listeners_router.delete("/{listener_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_listener(
    listener_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c: Container = _container(request)
    await DeleteLbListener(listeners=c.lb_listeners_repo, events=c.events).execute(
        LbListenerId(listener_id)
    )


# ---------------------------------------------------------------------------
# N4-06  LbPool
# ---------------------------------------------------------------------------

pools_router = APIRouter(prefix="/lb-pools", tags=["lb-pools"])


@pools_router.post("", status_code=status.HTTP_201_CREATED)
async def create_pool(
    body: LbPoolCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = CreateLbPoolCommand(
        name=body.name,
        lb_id=LoadBalancerId(body.lb_id),
        protocol=body.protocol,
        lb_algorithm=body.lb_algorithm,
        session_persistence=body.session_persistence,
        description=body.description,
        labels=body.labels,
    )
    pool = await CreateLbPool(
        pools=c.lb_pools_repo,
        load_balancers=c.load_balancers_repo,
        clock=c.clock,
        ids=c.ids,
        events=c.events,
    ).execute(cmd)
    return _pool_out(pool)


@pools_router.get("")
async def list_pools(
    lb_id: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> list[dict[str, Any]]:
    c: Container = _container(request)
    pools = await ListLbPools(pools=c.lb_pools_repo).execute(
        lb_id=LoadBalancerId(lb_id) if lb_id else None
    )
    return [_pool_out(p) for p in pools]


@pools_router.get("/{pool_id}")
async def get_pool(
    pool_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    pool = await GetLbPool(pools=c.lb_pools_repo).execute(LbPoolId(pool_id))
    return _pool_out(pool)


@pools_router.patch("/{pool_id}")
async def update_pool(
    pool_id: str,
    body: LbPoolUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = UpdateLbPoolCommand(
        pool_id=LbPoolId(pool_id),
        name=body.name,
        lb_algorithm=body.lb_algorithm,
        session_persistence=body.session_persistence,
        description=body.description,
        labels=body.labels,
    )
    pool = await UpdateLbPool(
        pools=c.lb_pools_repo, clock=c.clock, events=c.events
    ).execute(cmd)
    return _pool_out(pool)


@pools_router.delete("/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pool(
    pool_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c: Container = _container(request)
    await DeleteLbPool(pools=c.lb_pools_repo, events=c.events).execute(LbPoolId(pool_id))


# ---------------------------------------------------------------------------
# N4-06  LbMember
# ---------------------------------------------------------------------------

members_router = APIRouter(prefix="/lb-members", tags=["lb-members"])


@members_router.post("", status_code=status.HTTP_201_CREATED)
async def add_member(
    body: LbMemberAddRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = AddLbMemberCommand(
        pool_id=LbPoolId(body.pool_id),
        address=body.address,
        protocol_port=body.protocol_port,
        weight=body.weight,
        admin_state_up=body.admin_state_up,
    )
    member = await AddLbMember(
        members=c.lb_members_repo,
        pools=c.lb_pools_repo,
        clock=c.clock,
        ids=c.ids,
        events=c.events,
    ).execute(cmd)
    return _member_out(member)


@members_router.get("")
async def list_members(
    pool_id: str | None = None,
    request: Request = ...,  # type: ignore[assignment]
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> list[dict[str, Any]]:
    c: Container = _container(request)
    members = await ListLbMembers(members=c.lb_members_repo).execute(
        pool_id=LbPoolId(pool_id) if pool_id else None
    )
    return [_member_out(m) for m in members]


@members_router.get("/{member_id}")
async def get_member(
    member_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    m = await GetLbMember(members=c.lb_members_repo).execute(LbMemberId(member_id))
    return _member_out(m)


@members_router.patch("/{member_id}")
async def update_member(
    member_id: str,
    body: LbMemberUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    m = await UpdateLbMember(
        members=c.lb_members_repo, clock=c.clock, events=c.events
    ).execute(
        LbMemberId(member_id),
        weight=body.weight,
        admin_state_up=body.admin_state_up,
    )
    return _member_out(m)


@members_router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    member_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c: Container = _container(request)
    await RemoveLbMember(members=c.lb_members_repo, events=c.events).execute(
        LbMemberId(member_id)
    )


# ---------------------------------------------------------------------------
# N4-07  HealthMonitor
# ---------------------------------------------------------------------------

monitors_router = APIRouter(prefix="/health-monitors", tags=["health-monitors"])


@monitors_router.post("", status_code=status.HTTP_201_CREATED)
async def create_monitor(
    body: HealthMonitorCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = CreateHealthMonitorCommand(
        pool_id=LbPoolId(body.pool_id),
        check_type=body.check_type,
        delay=body.delay,
        timeout=body.timeout,
        max_retries=body.max_retries,
        url_path=body.url_path,
        http_method=body.http_method,
        expected_codes=body.expected_codes,
    )
    monitor = await CreateHealthMonitor(
        monitors=c.health_monitors_repo,
        pools=c.lb_pools_repo,
        clock=c.clock,
        ids=c.ids,
        events=c.events,
    ).execute(cmd)
    return _monitor_out(monitor)


@monitors_router.get("/{monitor_id}")
async def get_monitor(
    monitor_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    c: Container = _container(request)
    monitor = await GetHealthMonitor(monitors=c.health_monitors_repo).execute(
        HealthMonitorId(monitor_id)
    )
    return _monitor_out(monitor)


@monitors_router.patch("/{monitor_id}")
async def update_monitor(
    monitor_id: str,
    body: HealthMonitorUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    c: Container = _container(request)
    cmd = UpdateHealthMonitorCommand(
        monitor_id=HealthMonitorId(monitor_id),
        delay=body.delay,
        timeout=body.timeout,
        max_retries=body.max_retries,
        url_path=body.url_path,
        http_method=body.http_method,
        expected_codes=body.expected_codes,
    )
    monitor = await UpdateHealthMonitor(
        monitors=c.health_monitors_repo, clock=c.clock, events=c.events
    ).execute(cmd)
    return _monitor_out(monitor)


@monitors_router.delete("/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_monitor(
    monitor_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c: Container = _container(request)
    await DeleteHealthMonitor(monitors=c.health_monitors_repo, events=c.events).execute(
        HealthMonitorId(monitor_id)
    )
