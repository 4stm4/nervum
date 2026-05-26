"""N5 REST-роутеры — Advanced.

Маршруты:
  /schedules                              — CRUD cron-расписаний apply (N5-01)
  /schedules/{id}/toggle                  — вкл/выкл расписания (N5-01)
  /mirror-sessions                        — CRUD зеркалирования портов (N5-02)
  /mirror-sessions/{id}/apply             — генерация OVS-конфига (N5-02)
  /vpn-tunnels                            — CRUD VPN-туннелей (N5-05)
  /vpn-tunnels/{id}/apply                 — генерация wg/ipsec-конфига (N5-05)
  /vpn-tunnels/{id}/peers                 — управление WireGuard peer'ами (N5-05)

Права:
  NETWORK_READ  — список / детали
  NETWORK_WRITE — создание / изменение / удаление
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from sdn_controller.adapters.http_api.auth import require as require_permission
from sdn_controller.app.container import Container
from sdn_controller.core.use_cases.n5 import (
    AddVpnPeer,
    AddVpnPeerCommand,
    ApplyMirrorSession,
    ApplyVpnTunnel,
    CreateApplySchedule,
    CreateApplyScheduleCommand,
    CreateMirrorSession,
    CreateMirrorSessionCommand,
    CreateVpnTunnel,
    CreateVpnTunnelCommand,
    DeleteApplySchedule,
    DeleteMirrorSession,
    DeleteVpnTunnel,
    GetApplySchedule,
    GetMirrorSession,
    GetVpnPeer,
    GetVpnTunnel,
    ListApplySchedules,
    ListMirrorSessions,
    ListVpnPeers,
    ListVpnTunnels,
    RemoveVpnPeer,
    ToggleApplySchedule,
    UpdateApplySchedule,
    UpdateApplyScheduleCommand,
    UpdateVpnPeer,
    UpdateVpnPeerCommand,
    UpdateVpnTunnel,
    UpdateVpnTunnelCommand,
)
from sdn_controller.core.value_objects.ids import (
    ApplyScheduleId,
    MirrorSessionId,
    ProjectId,
    VpnPeerId,
    VpnTunnelId,
)
from sdn_controller.core.value_objects.security import Permission


def _container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Pydantic-схемы — ApplySchedule (N5-01)
# ---------------------------------------------------------------------------


class ScheduleCreateRequest(BaseModel):
    name: str
    cron_expr: str
    target_type: str
    target_id: str
    project_id: str | None = None
    enabled: bool = True
    labels: dict[str, str] = Field(default_factory=dict)


class ScheduleUpdateRequest(BaseModel):
    name: str | None = None
    cron_expr: str | None = None
    labels: dict[str, str] | None = None


class ScheduleToggleRequest(BaseModel):
    enabled: bool


class ScheduleOut(BaseModel):
    id: str
    name: str
    cron_expr: str
    target_type: str
    target_id: str
    enabled: bool
    project_id: str | None
    status: str
    last_run_at: str | None
    last_run_status: str | None
    labels: dict[str, str]


def _sched_out(s: object) -> ScheduleOut:
    return ScheduleOut(
        id=s.id,  # type: ignore[attr-defined]
        name=s.name,  # type: ignore[attr-defined]
        cron_expr=s.cron_expr,  # type: ignore[attr-defined]
        target_type=s.target_type.value,  # type: ignore[attr-defined]
        target_id=s.target_id,  # type: ignore[attr-defined]
        enabled=s.enabled,  # type: ignore[attr-defined]
        project_id=s.project_id,  # type: ignore[attr-defined]
        status=s.status.value,  # type: ignore[attr-defined]
        last_run_at=s.last_run_at.isoformat() if s.last_run_at else None,  # type: ignore[attr-defined]
        last_run_status=s.last_run_status,  # type: ignore[attr-defined]
        labels=dict(s.labels),  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# Pydantic-схемы — MirrorSession (N5-02)
# ---------------------------------------------------------------------------


class MirrorSessionCreateRequest(BaseModel):
    name: str
    source_port_id: str
    direction: str = "both"
    destination_port_id: str | None = None
    destination_ip: str | None = None
    filter_vlan: int | None = None
    project_id: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class MirrorSessionOut(BaseModel):
    id: str
    name: str
    source_port_id: str
    direction: str
    destination_port_id: str | None
    destination_ip: str | None
    filter_vlan: int | None
    project_id: str | None
    status: str
    applied_config: str | None
    applied_at: str | None
    labels: dict[str, str]


def _mirror_out(m: object) -> MirrorSessionOut:
    return MirrorSessionOut(
        id=m.id,  # type: ignore[attr-defined]
        name=m.name,  # type: ignore[attr-defined]
        source_port_id=m.source_port_id,  # type: ignore[attr-defined]
        direction=m.direction.value,  # type: ignore[attr-defined]
        destination_port_id=m.destination_port_id,  # type: ignore[attr-defined]
        destination_ip=m.destination_ip,  # type: ignore[attr-defined]
        filter_vlan=m.filter_vlan,  # type: ignore[attr-defined]
        project_id=m.project_id,  # type: ignore[attr-defined]
        status=m.status.value,  # type: ignore[attr-defined]
        applied_config=m.applied_config,  # type: ignore[attr-defined]
        applied_at=m.applied_at.isoformat() if m.applied_at else None,  # type: ignore[attr-defined]
        labels=dict(m.labels),  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# Pydantic-схемы — VpnTunnel / VpnPeer (N5-05)
# ---------------------------------------------------------------------------


class VpnTunnelCreateRequest(BaseModel):
    name: str
    protocol: str = "wireguard"
    local_endpoint: str
    remote_endpoint: str
    local_public_key: str
    remote_public_key: str
    listen_port: int = 51820
    preshared_key: str | None = None
    project_id: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class VpnTunnelUpdateRequest(BaseModel):
    name: str | None = None
    remote_public_key: str | None = None
    preshared_key: str | None = None
    labels: dict[str, str] | None = None


class VpnTunnelOut(BaseModel):
    id: str
    name: str
    protocol: str
    local_endpoint: str
    remote_endpoint: str
    local_public_key: str
    remote_public_key: str
    listen_port: int
    project_id: str | None
    status: str
    applied_config: str | None
    applied_at: str | None
    labels: dict[str, str]


def _tunnel_out(t: object) -> VpnTunnelOut:
    return VpnTunnelOut(
        id=t.id,  # type: ignore[attr-defined]
        name=t.name,  # type: ignore[attr-defined]
        protocol=t.protocol.value,  # type: ignore[attr-defined]
        local_endpoint=t.local_endpoint,  # type: ignore[attr-defined]
        remote_endpoint=t.remote_endpoint,  # type: ignore[attr-defined]
        local_public_key=t.local_public_key,  # type: ignore[attr-defined]
        remote_public_key=t.remote_public_key,  # type: ignore[attr-defined]
        listen_port=t.listen_port,  # type: ignore[attr-defined]
        project_id=t.project_id,  # type: ignore[attr-defined]
        status=t.status.value,  # type: ignore[attr-defined]
        applied_config=t.applied_config,  # type: ignore[attr-defined]
        applied_at=t.applied_at.isoformat() if t.applied_at else None,  # type: ignore[attr-defined]
        labels=dict(t.labels),  # type: ignore[attr-defined]
    )


class VpnPeerCreateRequest(BaseModel):
    public_key: str
    allowed_ips: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    persistent_keepalive: int = 0


class VpnPeerUpdateRequest(BaseModel):
    allowed_ips: list[str] | None = None
    endpoint: str | None = None
    persistent_keepalive: int | None = None


class VpnPeerOut(BaseModel):
    id: str
    tunnel_id: str
    public_key: str
    allowed_ips: list[str]
    endpoint: str | None
    persistent_keepalive: int


def _peer_out(p: object) -> VpnPeerOut:
    return VpnPeerOut(
        id=p.id,  # type: ignore[attr-defined]
        tunnel_id=p.tunnel_id,  # type: ignore[attr-defined]
        public_key=p.public_key,  # type: ignore[attr-defined]
        allowed_ips=list(p.allowed_ips),  # type: ignore[attr-defined]
        endpoint=p.endpoint,  # type: ignore[attr-defined]
        persistent_keepalive=p.persistent_keepalive,  # type: ignore[attr-defined]
    )


class ApplyOut(BaseModel):
    id: str
    config: str


# ===========================================================================
# N5-01  ApplySchedule — роутер
# ===========================================================================

schedules_router = APIRouter(prefix="/schedules", tags=["N5-01 schedules"])


@schedules_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ScheduleOut,
    summary="Создать cron-расписание apply",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def create_schedule(
    body: ScheduleCreateRequest,
    request: Request,
) -> ScheduleOut:
    c = _container(request)
    cmd = CreateApplyScheduleCommand(
        name=body.name,
        cron_expr=body.cron_expr,
        target_type=body.target_type,
        target_id=body.target_id,
        project_id=ProjectId(body.project_id) if body.project_id else None,
        enabled=body.enabled,
        labels=body.labels,
    )
    result = await c.create_apply_schedule.execute(cmd)
    return _sched_out(result)


@schedules_router.get(
    "",
    response_model=list[ScheduleOut],
    summary="Список расписаний",
    dependencies=[Depends(require_permission(Permission.NETWORK_READ))],
)
async def list_schedules(
    request: Request,
    project_id: str | None = None,
    enabled_only: bool = False,
) -> list[ScheduleOut]:
    c = _container(request)
    items = await c.list_apply_schedules.execute(
        project_id=ProjectId(project_id) if project_id else None,
        enabled_only=enabled_only,
    )
    return [_sched_out(s) for s in items]


@schedules_router.get(
    "/{schedule_id}",
    response_model=ScheduleOut,
    summary="Детали расписания",
    dependencies=[Depends(require_permission(Permission.NETWORK_READ))],
)
async def get_schedule(
    schedule_id: str,
    request: Request,
) -> ScheduleOut:
    c = _container(request)
    result = await c.get_apply_schedule.execute(ApplyScheduleId(schedule_id))
    return _sched_out(result)


@schedules_router.patch(
    "/{schedule_id}",
    response_model=ScheduleOut,
    summary="Обновить расписание",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def update_schedule(
    schedule_id: str,
    body: ScheduleUpdateRequest,
    request: Request,
) -> ScheduleOut:
    c = _container(request)
    cmd = UpdateApplyScheduleCommand(
        schedule_id=ApplyScheduleId(schedule_id),
        name=body.name,
        cron_expr=body.cron_expr,
        labels=body.labels,
    )
    result = await c.update_apply_schedule.execute(cmd)
    return _sched_out(result)


@schedules_router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить расписание",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def delete_schedule(
    schedule_id: str,
    request: Request,
) -> None:
    c = _container(request)
    await c.delete_apply_schedule.execute(ApplyScheduleId(schedule_id))


@schedules_router.post(
    "/{schedule_id}/toggle",
    response_model=ScheduleOut,
    summary="Включить / выключить расписание",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def toggle_schedule(
    schedule_id: str,
    body: ScheduleToggleRequest,
    request: Request,
) -> ScheduleOut:
    c = _container(request)
    result = await c.toggle_apply_schedule.execute(
        ApplyScheduleId(schedule_id), enabled=body.enabled
    )
    return _sched_out(result)


# ===========================================================================
# N5-02  MirrorSession — роутер
# ===========================================================================

mirror_sessions_router = APIRouter(
    prefix="/mirror-sessions", tags=["N5-02 mirror-sessions"]
)


@mirror_sessions_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=MirrorSessionOut,
    summary="Создать зеркальную сессию",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def create_mirror_session(
    body: MirrorSessionCreateRequest,
    request: Request,
) -> MirrorSessionOut:
    from sdn_controller.core.value_objects.ids import LogicalPortId

    c = _container(request)
    cmd = CreateMirrorSessionCommand(
        name=body.name,
        source_port_id=LogicalPortId(body.source_port_id),
        direction=body.direction,
        destination_port_id=LogicalPortId(body.destination_port_id)
        if body.destination_port_id
        else None,
        destination_ip=body.destination_ip,
        filter_vlan=body.filter_vlan,
        project_id=ProjectId(body.project_id) if body.project_id else None,
        labels=body.labels,
    )
    result = await c.create_mirror_session.execute(cmd)
    return _mirror_out(result)


@mirror_sessions_router.get(
    "",
    response_model=list[MirrorSessionOut],
    summary="Список зеркальных сессий",
    dependencies=[Depends(require_permission(Permission.NETWORK_READ))],
)
async def list_mirror_sessions(
    request: Request,
    project_id: str | None = None,
) -> list[MirrorSessionOut]:
    c = _container(request)
    items = await c.list_mirror_sessions.execute(
        project_id=ProjectId(project_id) if project_id else None,
    )
    return [_mirror_out(m) for m in items]


@mirror_sessions_router.get(
    "/{session_id}",
    response_model=MirrorSessionOut,
    summary="Детали зеркальной сессии",
    dependencies=[Depends(require_permission(Permission.NETWORK_READ))],
)
async def get_mirror_session(
    session_id: str,
    request: Request,
) -> MirrorSessionOut:
    c = _container(request)
    result = await c.get_mirror_session.execute(MirrorSessionId(session_id))
    return _mirror_out(result)


@mirror_sessions_router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить зеркальную сессию",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def delete_mirror_session(
    session_id: str,
    request: Request,
) -> None:
    c = _container(request)
    await c.delete_mirror_session.execute(MirrorSessionId(session_id))


@mirror_sessions_router.post(
    "/{session_id}/apply",
    response_model=ApplyOut,
    summary="Применить зеркальную сессию (генерация OVS-конфига)",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def apply_mirror_session(
    session_id: str,
    request: Request,
) -> ApplyOut:
    c = _container(request)
    result = await c.apply_mirror_session.execute(MirrorSessionId(session_id))
    return ApplyOut(id=result.id, config=result.applied_config or "")


# ===========================================================================
# N5-05  VpnTunnel — роутер
# ===========================================================================

vpn_tunnels_router = APIRouter(prefix="/vpn-tunnels", tags=["N5-05 vpn"])


@vpn_tunnels_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=VpnTunnelOut,
    summary="Создать VPN-туннель",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def create_vpn_tunnel(
    body: VpnTunnelCreateRequest,
    request: Request,
) -> VpnTunnelOut:
    c = _container(request)
    cmd = CreateVpnTunnelCommand(
        name=body.name,
        protocol=body.protocol,
        local_endpoint=body.local_endpoint,
        remote_endpoint=body.remote_endpoint,
        local_public_key=body.local_public_key,
        remote_public_key=body.remote_public_key,
        listen_port=body.listen_port,
        preshared_key=body.preshared_key,
        project_id=ProjectId(body.project_id) if body.project_id else None,
        labels=body.labels,
    )
    result = await c.create_vpn_tunnel.execute(cmd)
    return _tunnel_out(result)


@vpn_tunnels_router.get(
    "",
    response_model=list[VpnTunnelOut],
    summary="Список VPN-туннелей",
    dependencies=[Depends(require_permission(Permission.NETWORK_READ))],
)
async def list_vpn_tunnels(
    request: Request,
    project_id: str | None = None,
) -> list[VpnTunnelOut]:
    c = _container(request)
    items = await c.list_vpn_tunnels.execute(
        project_id=ProjectId(project_id) if project_id else None,
    )
    return [_tunnel_out(t) for t in items]


@vpn_tunnels_router.get(
    "/{tunnel_id}",
    response_model=VpnTunnelOut,
    summary="Детали VPN-туннеля",
    dependencies=[Depends(require_permission(Permission.NETWORK_READ))],
)
async def get_vpn_tunnel(
    tunnel_id: str,
    request: Request,
) -> VpnTunnelOut:
    c = _container(request)
    result = await c.get_vpn_tunnel.execute(VpnTunnelId(tunnel_id))
    return _tunnel_out(result)


@vpn_tunnels_router.patch(
    "/{tunnel_id}",
    response_model=VpnTunnelOut,
    summary="Обновить VPN-туннель",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def update_vpn_tunnel(
    tunnel_id: str,
    body: VpnTunnelUpdateRequest,
    request: Request,
) -> VpnTunnelOut:
    c = _container(request)
    cmd = UpdateVpnTunnelCommand(
        tunnel_id=VpnTunnelId(tunnel_id),
        name=body.name,
        remote_public_key=body.remote_public_key,
        preshared_key=body.preshared_key,
        labels=body.labels,
    )
    result = await c.update_vpn_tunnel.execute(cmd)
    return _tunnel_out(result)


@vpn_tunnels_router.delete(
    "/{tunnel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить VPN-туннель (каскадно удаляет peer'ы)",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def delete_vpn_tunnel(
    tunnel_id: str,
    request: Request,
) -> None:
    c = _container(request)
    await c.delete_vpn_tunnel.execute(VpnTunnelId(tunnel_id))


@vpn_tunnels_router.post(
    "/{tunnel_id}/apply",
    response_model=ApplyOut,
    summary="Генерация конфига WireGuard / IPsec",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def apply_vpn_tunnel(
    tunnel_id: str,
    request: Request,
) -> ApplyOut:
    c = _container(request)
    result = await c.apply_vpn_tunnel.execute(VpnTunnelId(tunnel_id))
    return ApplyOut(id=result.id, config=result.applied_config or "")


# ---------------------------------------------------------------------------
# VpnPeer — вложенный роутер под /vpn-tunnels/{tunnel_id}/peers
# ---------------------------------------------------------------------------

vpn_peers_router = APIRouter(
    prefix="/vpn-tunnels/{tunnel_id}/peers", tags=["N5-05 vpn"]
)


@vpn_peers_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=VpnPeerOut,
    summary="Добавить WireGuard peer",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def add_vpn_peer(
    tunnel_id: str,
    body: VpnPeerCreateRequest,
    request: Request,
) -> VpnPeerOut:
    c = _container(request)
    cmd = AddVpnPeerCommand(
        tunnel_id=VpnTunnelId(tunnel_id),
        public_key=body.public_key,
        allowed_ips=body.allowed_ips,
        endpoint=body.endpoint,
        persistent_keepalive=body.persistent_keepalive,
    )
    result = await c.add_vpn_peer.execute(cmd)
    return _peer_out(result)


@vpn_peers_router.get(
    "",
    response_model=list[VpnPeerOut],
    summary="Список peer'ов туннеля",
    dependencies=[Depends(require_permission(Permission.NETWORK_READ))],
)
async def list_vpn_peers(
    tunnel_id: str,
    request: Request,
) -> list[VpnPeerOut]:
    c = _container(request)
    items = await c.list_vpn_peers.execute(
        tunnel_id=VpnTunnelId(tunnel_id),
    )
    return [_peer_out(p) for p in items]


@vpn_peers_router.get(
    "/{peer_id}",
    response_model=VpnPeerOut,
    summary="Детали peer'а",
    dependencies=[Depends(require_permission(Permission.NETWORK_READ))],
)
async def get_vpn_peer(
    tunnel_id: str,
    peer_id: str,
    request: Request,
) -> VpnPeerOut:
    c = _container(request)
    result = await c.get_vpn_peer.execute(VpnPeerId(peer_id))
    return _peer_out(result)


@vpn_peers_router.patch(
    "/{peer_id}",
    response_model=VpnPeerOut,
    summary="Обновить peer",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def update_vpn_peer(
    tunnel_id: str,
    peer_id: str,
    body: VpnPeerUpdateRequest,
    request: Request,
) -> VpnPeerOut:
    c = _container(request)
    cmd = UpdateVpnPeerCommand(
        peer_id=VpnPeerId(peer_id),
        allowed_ips=body.allowed_ips,
        endpoint=body.endpoint,
        persistent_keepalive=body.persistent_keepalive,
    )
    result = await c.update_vpn_peer.execute(cmd)
    return _peer_out(result)


@vpn_peers_router.delete(
    "/{peer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить peer",
    dependencies=[Depends(require_permission(Permission.NETWORK_WRITE))],
)
async def delete_vpn_peer(
    tunnel_id: str,
    peer_id: str,
    request: Request,
) -> None:
    c = _container(request)
    await c.remove_vpn_peer.execute(VpnPeerId(peer_id))
