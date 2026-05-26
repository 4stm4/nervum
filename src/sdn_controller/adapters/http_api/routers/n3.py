"""N3 REST-роутеры — Router, FloatingIP, BgpPeer.

Маршруты:
  /routers                               — CRUD маршрутизаторов
  /routers/{id}/routes                   — управление статическими маршрутами
  /routers/{id}/networks                 — подключение/отключение внутренних сетей
  /routers/{id}/apply                    — генерация конфига (N3-03)
  /routers/{id}/admin-state              — включение/выключение
  /floating-ips                          — allocate / list / get / release
  /floating-ips/{id}/associate           — привязка к порту (N3-02)
  /floating-ips/{id}/disassociate        — снятие привязки (N3-02)
  /bgp-peers                             — CRUD BGP-пиров (N3-05)
  /bgp-peers/{id}/state                  — обновление состояния сессии

Права:
  NETWORK_READ  — список / детали
  NETWORK_WRITE — создание / изменение / применение / удаление
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from sdn_controller.adapters.http_api.auth import require as require_permission
from sdn_controller.app.container import Container
from sdn_controller.core.use_cases.n3 import (
    AddInternalNetwork,
    AddStaticRoute,
    AllocateFloatingIp,
    AllocateFloatingIpCommand,
    ApplyRouter,
    AssociateFloatingIp,
    AssociateFloatingIpCommand,
    CreateBgpPeer,
    CreateBgpPeerCommand,
    CreateRouter,
    CreateRouterCommand,
    DeleteBgpPeer,
    DeleteRouter,
    DisassociateFloatingIp,
    GetBgpPeer,
    GetFloatingIp,
    GetRouter,
    ListBgpPeers,
    ListFloatingIps,
    ListRouters,
    ReleaseFloatingIp,
    RemoveInternalNetwork,
    RemoveStaticRoute,
    SetRouterAdminState,
    UpdateBgpPeerState,
    UpdateRouter,
    UpdateRouterCommand,
)
from sdn_controller.core.value_objects.ids import (
    BgpPeerId,
    FloatingIpId,
    LogicalPortId,
    NetworkId,
    ProjectId,
    RouterId,
)
from sdn_controller.core.value_objects.security import Permission


def _container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Pydantic-схемы — Router
# ---------------------------------------------------------------------------


class RouterCreateRequest(BaseModel):
    name: str
    description: str = ""
    project_id: str | None = None
    external_network_id: str | None = None
    ha_mode: str = "none"
    vrrp_priority: int | None = None
    vrrp_vrid: int | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class RouterUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    external_network_id: str | None = None
    ha_mode: str | None = None
    vrrp_priority: int | None = None
    vrrp_vrid: int | None = None
    ipv6_mode: str | None = None
    ipv6_prefix: str | None = None
    ipv6_dhcpv6_stateful: bool | None = None
    labels: dict[str, str] | None = None


class StaticRouteAddRequest(BaseModel):
    destination: str
    nexthop: str


class InternalNetworkRequest(BaseModel):
    network_id: str


class AdminStateRequest(BaseModel):
    admin_state_up: bool


class IPv6ConfigOut(BaseModel):
    mode: str
    prefix: str
    dhcpv6_stateful: bool


class RouterOut(BaseModel):
    id: str
    name: str
    description: str
    project_id: str | None
    external_network_id: str | None
    internal_network_ids: list[str]
    static_routes: list[dict[str, str]]
    status: str
    admin_state_up: bool
    ha_mode: str
    vrrp_priority: int | None
    vrrp_vrid: int | None
    ipv6_config: IPv6ConfigOut | None
    applied_config: str | None
    applied_at: str | None
    labels: dict[str, str]
    created_at: str
    updated_at: str


class RouterListResponse(BaseModel):
    items: list[RouterOut]


# ---------------------------------------------------------------------------
# Pydantic-схемы — FloatingIP
# ---------------------------------------------------------------------------


class FloatingIpAllocateRequest(BaseModel):
    external_network_id: str
    floating_ip_address: str
    project_id: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class FloatingIpAssociateRequest(BaseModel):
    logical_port_id: str
    fixed_ip_address: str
    router_id: str


class FloatingIpOut(BaseModel):
    id: str
    external_network_id: str
    floating_ip_address: str
    project_id: str | None
    fixed_ip_address: str | None
    logical_port_id: str | None
    router_id: str | None
    status: str
    labels: dict[str, str]
    created_at: str
    updated_at: str


class FloatingIpListResponse(BaseModel):
    items: list[FloatingIpOut]


# ---------------------------------------------------------------------------
# Pydantic-схемы — BgpPeer
# ---------------------------------------------------------------------------


class BgpPeerCreateRequest(BaseModel):
    router_id: str
    peer_ip: str
    peer_asn: int
    local_asn: int
    password: str = ""
    project_id: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class BgpPeerStateRequest(BaseModel):
    state: str


class BgpPeerOut(BaseModel):
    id: str
    router_id: str
    peer_ip: str
    peer_asn: int
    local_asn: int
    state: str
    project_id: str | None
    labels: dict[str, str]
    created_at: str
    updated_at: str


class BgpPeerListResponse(BaseModel):
    items: list[BgpPeerOut]


# ---------------------------------------------------------------------------
# Сериализаторы
# ---------------------------------------------------------------------------


def _router_out(router: Any) -> RouterOut:
    ipv6: IPv6ConfigOut | None = None
    if router.ipv6_config:
        cfg = router.ipv6_config
        ipv6 = IPv6ConfigOut(
            mode=cfg.mode,
            prefix=cfg.prefix,
            dhcpv6_stateful=cfg.dhcpv6_stateful,
        )
    return RouterOut(
        id=router.id,
        name=router.name,
        description=router.description,
        project_id=router.project_id,
        external_network_id=router.external_network_id,
        internal_network_ids=sorted(router.internal_network_ids),
        static_routes=[
            {"destination": r.destination, "nexthop": r.nexthop}
            for r in router.static_routes
        ],
        status=router.status,
        admin_state_up=router.admin_state_up,
        ha_mode=router.ha_mode,
        vrrp_priority=router.vrrp_priority,
        vrrp_vrid=router.vrrp_vrid,
        ipv6_config=ipv6,
        applied_config=router.applied_config,
        applied_at=router.applied_at.isoformat() if router.applied_at else None,
        labels=router.labels,
        created_at=router.created_at.isoformat(),
        updated_at=router.updated_at.isoformat(),
    )


def _fip_out(fip: Any) -> FloatingIpOut:
    return FloatingIpOut(
        id=fip.id,
        external_network_id=fip.external_network_id,
        floating_ip_address=fip.floating_ip_address,
        project_id=fip.project_id,
        fixed_ip_address=fip.fixed_ip_address,
        logical_port_id=fip.logical_port_id,
        router_id=fip.router_id,
        status=fip.status,
        labels=fip.labels,
        created_at=fip.created_at.isoformat(),
        updated_at=fip.updated_at.isoformat(),
    )


def _bgp_out(peer: Any) -> BgpPeerOut:
    return BgpPeerOut(
        id=peer.id,
        router_id=peer.router_id,
        peer_ip=peer.peer_ip,
        peer_asn=peer.peer_asn,
        local_asn=peer.local_asn,
        state=peer.state,
        project_id=peer.project_id,
        labels=peer.labels,
        created_at=peer.created_at.isoformat(),
        updated_at=peer.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Router API
# ---------------------------------------------------------------------------

routers_router = APIRouter(tags=["routers"])


@routers_router.post(
    "/routers",
    status_code=status.HTTP_201_CREATED,
    summary="Создать L3-маршрутизатор (N3-01)",
)
async def create_router(
    body: RouterCreateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: CreateRouter = _container(request).create_router
    router = await uc.execute(
        CreateRouterCommand(
            name=body.name,
            description=body.description,
            project_id=ProjectId(body.project_id) if body.project_id else None,
            external_network_id=NetworkId(body.external_network_id)
            if body.external_network_id
            else None,
            ha_mode=body.ha_mode,
            vrrp_priority=body.vrrp_priority,
            vrrp_vrid=body.vrrp_vrid,
            labels=body.labels,
        )
    )
    return {"router": _router_out(router).model_dump()}


@routers_router.get("/routers", summary="Список маршрутизаторов")
async def list_routers(
    request: Request,
    project_id: str | None = None,
    _auth: Any = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    uc: ListRouters = _container(request).list_routers
    routers = await uc.execute(
        project_id=ProjectId(project_id) if project_id else None,
    )
    return {"items": [_router_out(r).model_dump() for r in routers]}


@routers_router.get("/routers/{router_id}", summary="Получить маршрутизатор")
async def get_router(
    router_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    uc: GetRouter = _container(request).get_router
    router = await uc.execute(RouterId(router_id))
    return {"router": _router_out(router).model_dump()}


@routers_router.patch("/routers/{router_id}", summary="Обновить маршрутизатор")
async def update_router(
    router_id: str,
    body: RouterUpdateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: UpdateRouter = _container(request).update_router
    router = await uc.execute(
        UpdateRouterCommand(
            router_id=RouterId(router_id),
            name=body.name,
            description=body.description,
            external_network_id=NetworkId(body.external_network_id)
            if body.external_network_id is not None
            else None,
            ha_mode=body.ha_mode,
            vrrp_priority=body.vrrp_priority,
            vrrp_vrid=body.vrrp_vrid,
            ipv6_mode=body.ipv6_mode,
            ipv6_prefix=body.ipv6_prefix,
            ipv6_dhcpv6_stateful=body.ipv6_dhcpv6_stateful,
            labels=body.labels,
        )
    )
    return {"router": _router_out(router).model_dump()}


@routers_router.delete(
    "/routers/{router_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить маршрутизатор",
)
async def delete_router(
    router_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    uc: DeleteRouter = _container(request).delete_router
    await uc.execute(RouterId(router_id))


# Статические маршруты (sub-resource)


@routers_router.post(
    "/routers/{router_id}/routes",
    status_code=status.HTTP_201_CREATED,
    summary="Добавить статический маршрут (N3-01)",
)
async def add_static_route(
    router_id: str,
    body: StaticRouteAddRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: AddStaticRoute = _container(request).add_static_route
    router = await uc.execute(
        RouterId(router_id),
        destination=body.destination,
        nexthop=body.nexthop,
    )
    return {"router": _router_out(router).model_dump()}


@routers_router.delete(
    "/routers/{router_id}/routes/{destination:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить статический маршрут (N3-01)",
)
async def remove_static_route(
    router_id: str,
    destination: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    uc: RemoveStaticRoute = _container(request).remove_static_route
    await uc.execute(RouterId(router_id), destination=destination)


# Внутренние сети (sub-resource)


@routers_router.post(
    "/routers/{router_id}/networks",
    status_code=status.HTTP_201_CREATED,
    summary="Подключить внутреннюю сеть к маршрутизатору (N3-01)",
)
async def add_internal_network(
    router_id: str,
    body: InternalNetworkRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: AddInternalNetwork = _container(request).add_internal_network
    router = await uc.execute(RouterId(router_id), network_id=NetworkId(body.network_id))
    return {"router": _router_out(router).model_dump()}


@routers_router.delete(
    "/routers/{router_id}/networks/{network_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отключить внутреннюю сеть от маршрутизатора (N3-01)",
)
async def remove_internal_network(
    router_id: str,
    network_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    uc: RemoveInternalNetwork = _container(request).remove_internal_network
    await uc.execute(RouterId(router_id), network_id=NetworkId(network_id))


# Lifecycle actions


@routers_router.post(
    "/routers/{router_id}/apply",
    summary="Сгенерировать и применить конфиг маршрутизатора (N3-03)",
)
async def apply_router(
    router_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: ApplyRouter = _container(request).apply_router
    router = await uc.execute(RouterId(router_id))
    return {"router": _router_out(router).model_dump()}


@routers_router.put(
    "/routers/{router_id}/admin-state",
    summary="Включить/выключить маршрутизатор",
)
async def set_router_admin_state(
    router_id: str,
    body: AdminStateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: SetRouterAdminState = _container(request).set_router_admin_state
    router = await uc.execute(RouterId(router_id), up=body.admin_state_up)
    return {"router": _router_out(router).model_dump()}


# ---------------------------------------------------------------------------
# FloatingIP API
# ---------------------------------------------------------------------------

floating_ips_router = APIRouter(tags=["floating-ips"])


@floating_ips_router.post(
    "/floating-ips",
    status_code=status.HTTP_201_CREATED,
    summary="Выделить Floating IP (N3-02)",
)
async def allocate_floating_ip(
    body: FloatingIpAllocateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: AllocateFloatingIp = _container(request).allocate_floating_ip
    fip = await uc.execute(
        AllocateFloatingIpCommand(
            external_network_id=NetworkId(body.external_network_id),
            floating_ip_address=body.floating_ip_address,
            project_id=ProjectId(body.project_id) if body.project_id else None,
            labels=body.labels,
        )
    )
    return {"floating_ip": _fip_out(fip).model_dump()}


@floating_ips_router.get("/floating-ips", summary="Список Floating IP")
async def list_floating_ips(
    request: Request,
    project_id: str | None = None,
    router_id: str | None = None,
    _auth: Any = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    uc: ListFloatingIps = _container(request).list_floating_ips
    fips = await uc.execute(
        project_id=ProjectId(project_id) if project_id else None,
        router_id=RouterId(router_id) if router_id else None,
    )
    return {"items": [_fip_out(f).model_dump() for f in fips]}


@floating_ips_router.get("/floating-ips/{fip_id}", summary="Получить Floating IP")
async def get_floating_ip(
    fip_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    uc: GetFloatingIp = _container(request).get_floating_ip
    fip = await uc.execute(FloatingIpId(fip_id))
    return {"floating_ip": _fip_out(fip).model_dump()}


@floating_ips_router.post(
    "/floating-ips/{fip_id}/associate",
    summary="Привязать Floating IP к порту (N3-02)",
)
async def associate_floating_ip(
    fip_id: str,
    body: FloatingIpAssociateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: AssociateFloatingIp = _container(request).associate_floating_ip
    fip = await uc.execute(
        AssociateFloatingIpCommand(
            fip_id=FloatingIpId(fip_id),
            logical_port_id=LogicalPortId(body.logical_port_id),
            fixed_ip_address=body.fixed_ip_address,
            router_id=RouterId(body.router_id),
        )
    )
    return {"floating_ip": _fip_out(fip).model_dump()}


@floating_ips_router.post(
    "/floating-ips/{fip_id}/disassociate",
    summary="Снять привязку Floating IP (N3-02)",
)
async def disassociate_floating_ip(
    fip_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: DisassociateFloatingIp = _container(request).disassociate_floating_ip
    fip = await uc.execute(FloatingIpId(fip_id))
    return {"floating_ip": _fip_out(fip).model_dump()}


@floating_ips_router.delete(
    "/floating-ips/{fip_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Освободить Floating IP (N3-02)",
)
async def release_floating_ip(
    fip_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    uc: ReleaseFloatingIp = _container(request).release_floating_ip
    await uc.execute(FloatingIpId(fip_id))


# ---------------------------------------------------------------------------
# BgpPeer API
# ---------------------------------------------------------------------------

bgp_peers_router = APIRouter(tags=["bgp-peers"])


@bgp_peers_router.post(
    "/bgp-peers",
    status_code=status.HTTP_201_CREATED,
    summary="Создать BGP-пир (N3-05)",
)
async def create_bgp_peer(
    body: BgpPeerCreateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: CreateBgpPeer = _container(request).create_bgp_peer
    peer = await uc.execute(
        CreateBgpPeerCommand(
            router_id=RouterId(body.router_id),
            peer_ip=body.peer_ip,
            peer_asn=body.peer_asn,
            local_asn=body.local_asn,
            password=body.password,
            project_id=ProjectId(body.project_id) if body.project_id else None,
            labels=body.labels,
        )
    )
    return {"bgp_peer": _bgp_out(peer).model_dump()}


@bgp_peers_router.get("/bgp-peers", summary="Список BGP-пиров")
async def list_bgp_peers(
    request: Request,
    router_id: str | None = None,
    project_id: str | None = None,
    _auth: Any = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    uc: ListBgpPeers = _container(request).list_bgp_peers
    peers = await uc.execute(
        router_id=RouterId(router_id) if router_id else None,
        project_id=ProjectId(project_id) if project_id else None,
    )
    return {"items": [_bgp_out(p).model_dump() for p in peers]}


@bgp_peers_router.get("/bgp-peers/{peer_id}", summary="Получить BGP-пир")
async def get_bgp_peer(
    peer_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    uc: GetBgpPeer = _container(request).get_bgp_peer
    peer = await uc.execute(BgpPeerId(peer_id))
    return {"bgp_peer": _bgp_out(peer).model_dump()}


@bgp_peers_router.delete(
    "/bgp-peers/{peer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить BGP-пир",
)
async def delete_bgp_peer(
    peer_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    uc: DeleteBgpPeer = _container(request).delete_bgp_peer
    await uc.execute(BgpPeerId(peer_id))


@bgp_peers_router.put(
    "/bgp-peers/{peer_id}/state",
    summary="Обновить состояние BGP-сессии",
)
async def update_bgp_peer_state(
    peer_id: str,
    body: BgpPeerStateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: UpdateBgpPeerState = _container(request).update_bgp_peer_state
    peer = await uc.execute(BgpPeerId(peer_id), state=body.state)
    return {"bgp_peer": _bgp_out(peer).model_dump()}
