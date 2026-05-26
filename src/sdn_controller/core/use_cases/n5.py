"""N5 — Advanced use cases.

N5-01  Schedule  — cron-расписания автоматического apply
N5-02  Mirror    — port mirroring (SPAN / ERSPAN)
N5-05  VPNaaS   — WireGuard / IPsec туннели и peer'ы

(N5-03 gRPC и N5-04 PgAdvisoryLocks — инфраструктурные адаптеры,
 не требуют отдельных use cases в доменном слое.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import structlog

from sdn_controller.core.entities.apply_schedule import ApplySchedule
from sdn_controller.core.entities.mirror_session import MirrorSession
from sdn_controller.core.entities.vpn_tunnel import VpnPeer, VpnTunnel
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.services.cron_scheduler import CronScheduler
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.services.mirror_configurator import MirrorConfigurator
from sdn_controller.core.services.vpn_configurator import VpnConfigurator
from sdn_controller.core.value_objects.enums import (
    MirrorDirection,
    ScheduleTargetType,
    VpnProtocol,
)
from sdn_controller.core.value_objects.errors import ConflictError, NotFoundError
from sdn_controller.core.value_objects.ids import (
    ApplyScheduleId,
    IdFactory,
    LogicalPortId,
    MirrorSessionId,
    ProjectId,
    VpnPeerId,
    VpnTunnelId,
)

_log = structlog.get_logger(__name__)

__all__ = [
    # Schedule
    "CreateApplyScheduleCommand",
    "CreateApplySchedule",
    "GetApplySchedule",
    "ListApplySchedules",
    "UpdateApplyScheduleCommand",
    "UpdateApplySchedule",
    "DeleteApplySchedule",
    "ToggleApplySchedule",
    "TickScheduler",
    # Mirror
    "CreateMirrorSessionCommand",
    "CreateMirrorSession",
    "GetMirrorSession",
    "ListMirrorSessions",
    "DeleteMirrorSession",
    "ApplyMirrorSession",
    # VPN
    "CreateVpnTunnelCommand",
    "CreateVpnTunnel",
    "GetVpnTunnel",
    "ListVpnTunnels",
    "UpdateVpnTunnelCommand",
    "UpdateVpnTunnel",
    "DeleteVpnTunnel",
    "ApplyVpnTunnel",
    "AddVpnPeerCommand",
    "AddVpnPeer",
    "GetVpnPeer",
    "ListVpnPeers",
    "UpdateVpnPeerCommand",
    "UpdateVpnPeer",
    "RemoveVpnPeer",
]


# ---------------------------------------------------------------------------
# Репозиторные протоколы (импорт из ports.persistence)
# ---------------------------------------------------------------------------

class ApplyScheduleRepository(Protocol):
    async def get(self, schedule_id: ApplyScheduleId) -> ApplySchedule | None: ...
    async def list(
        self,
        *,
        enabled_only: bool = False,
        project_id: ProjectId | None = None,
    ) -> list[ApplySchedule]: ...
    async def save(self, schedule: ApplySchedule) -> None: ...
    async def delete(self, schedule_id: ApplyScheduleId) -> None: ...


class MirrorSessionRepository(Protocol):
    async def get(self, session_id: MirrorSessionId) -> MirrorSession | None: ...
    async def list(self, *, project_id: ProjectId | None = None) -> list[MirrorSession]: ...
    async def save(self, session: MirrorSession) -> None: ...
    async def delete(self, session_id: MirrorSessionId) -> None: ...


class VpnTunnelRepository(Protocol):
    async def get(self, tunnel_id: VpnTunnelId) -> VpnTunnel | None: ...
    async def list(self, *, project_id: ProjectId | None = None) -> list[VpnTunnel]: ...
    async def save(self, tunnel: VpnTunnel) -> None: ...
    async def delete(self, tunnel_id: VpnTunnelId) -> None: ...


class VpnPeerRepository(Protocol):
    async def get(self, peer_id: VpnPeerId) -> VpnPeer | None: ...
    async def list(self, *, tunnel_id: VpnTunnelId | None = None) -> list[VpnPeer]: ...
    async def save(self, peer: VpnPeer) -> None: ...
    async def delete(self, peer_id: VpnPeerId) -> None: ...


# ===========================================================================
# N5-01  ApplySchedule
# ===========================================================================


@dataclass(frozen=True, slots=True)
class CreateApplyScheduleCommand:
    """Создать расписание apply."""

    name: str
    cron_expr: str
    target_type: str   # ScheduleTargetType value
    target_id: str
    project_id: ProjectId | None = None
    enabled: bool = True
    labels: dict[str, str] = field(default_factory=dict)


class CreateApplySchedule:
    """Создать cron-расписание автоматического apply (N5-01)."""

    def __init__(
        self,
        *,
        schedules: ApplyScheduleRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._schedules = schedules
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateApplyScheduleCommand) -> ApplySchedule:
        CronScheduler.validate(cmd.cron_expr)
        now = self._clock.now()
        schedule = ApplySchedule(
            id=self._ids.apply_schedule(),
            name=cmd.name,
            cron_expr=cmd.cron_expr,
            target_type=ScheduleTargetType(cmd.target_type),
            target_id=cmd.target_id,
            enabled=cmd.enabled,
            project_id=cmd.project_id,
            labels=dict(cmd.labels),
            created_at=now,
            updated_at=now,
        )
        await self._schedules.save(schedule)
        await self._events.publish(
            event_type="schedule.created",
            resource_type="apply_schedule",
            resource_id=schedule.id,
            payload={"target_type": cmd.target_type, "target_id": cmd.target_id},
            project_id=cmd.project_id,
        )
        return schedule


class GetApplySchedule:
    """Получить расписание по ID."""

    def __init__(self, *, schedules: ApplyScheduleRepository) -> None:
        self._schedules = schedules

    async def execute(self, schedule_id: ApplyScheduleId) -> ApplySchedule:
        schedule = await self._schedules.get(schedule_id)
        if schedule is None:
            raise NotFoundError(f"apply_schedule {schedule_id!r} не найдено")
        return schedule


class ListApplySchedules:
    """Список расписаний (с необязательными фильтрами)."""

    def __init__(self, *, schedules: ApplyScheduleRepository) -> None:
        self._schedules = schedules

    async def execute(
        self,
        *,
        enabled_only: bool = False,
        project_id: ProjectId | None = None,
    ) -> list[ApplySchedule]:
        return await self._schedules.list(
            enabled_only=enabled_only, project_id=project_id
        )


@dataclass(frozen=True, slots=True)
class UpdateApplyScheduleCommand:
    schedule_id: ApplyScheduleId
    name: str | None = None
    cron_expr: str | None = None
    labels: dict[str, str] | None = None


class UpdateApplySchedule:
    """Обновить имя / cron_expr / метки расписания."""

    def __init__(
        self,
        *,
        schedules: ApplyScheduleRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._schedules = schedules
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateApplyScheduleCommand) -> ApplySchedule:
        schedule = await self._schedules.get(cmd.schedule_id)
        if schedule is None:
            raise NotFoundError(f"apply_schedule {cmd.schedule_id!r} не найдено")
        now = self._clock.now()
        if cmd.name is not None:
            schedule.name = cmd.name
        if cmd.cron_expr is not None:
            CronScheduler.validate(cmd.cron_expr)
            schedule.cron_expr = cmd.cron_expr
        if cmd.labels is not None:
            schedule.labels = dict(cmd.labels)
        schedule.updated_at = now
        await self._schedules.save(schedule)
        return schedule


class DeleteApplySchedule:
    """Удалить расписание."""

    def __init__(
        self, *, schedules: ApplyScheduleRepository, events: EventPublisher
    ) -> None:
        self._schedules = schedules
        self._events = events

    async def execute(self, schedule_id: ApplyScheduleId) -> None:
        schedule = await self._schedules.get(schedule_id)
        if schedule is None:
            raise NotFoundError(f"apply_schedule {schedule_id!r} не найдено")
        await self._schedules.delete(schedule_id)
        await self._events.publish(
            event_type="schedule.deleted",
            resource_type="apply_schedule",
            resource_id=schedule_id,
            payload={},
            project_id=schedule.project_id,
        )


class ToggleApplySchedule:
    """Включить / приостановить расписание (N5-01)."""

    def __init__(
        self,
        *,
        schedules: ApplyScheduleRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._schedules = schedules
        self._clock = clock
        self._events = events

    async def execute(
        self, schedule_id: ApplyScheduleId, *, enabled: bool
    ) -> ApplySchedule:
        schedule = await self._schedules.get(schedule_id)
        if schedule is None:
            raise NotFoundError(f"apply_schedule {schedule_id!r} не найдено")
        now = self._clock.now()
        if enabled:
            schedule.enable(now)
        else:
            schedule.pause(now)
        await self._schedules.save(schedule)
        return schedule


class TickScheduler:
    """Фоновая задача: запустить все просроченные расписания (N5-01).

    ``apply_callbacks`` — словарь target_type → async callable(target_id) → None.
    Каждый колбэк вызывает соответствующий apply use case.
    """

    def __init__(
        self,
        *,
        schedules: ApplyScheduleRepository,
        clock: Clock,
        apply_callbacks: dict[ScheduleTargetType, object] | None = None,
    ) -> None:
        self._schedules = schedules
        self._clock = clock
        self._callbacks: dict[ScheduleTargetType, object] = apply_callbacks or {}

    async def execute(self) -> int:
        """Проверить расписания и выполнить готовые. Вернуть количество запущенных."""
        now = self._clock.now()
        enabled = await self._schedules.list(enabled_only=True)
        fired = 0
        for schedule in enabled:
            if not CronScheduler.is_due(schedule.cron_expr, now):
                continue
            callback = self._callbacks.get(schedule.target_type)
            success = True
            error_msg: str | None = None
            if callback is not None:
                import asyncio
                try:
                    import inspect
                    if inspect.iscoroutinefunction(callback):
                        await callback(schedule.target_id)  # type: ignore[operator]
                    else:
                        callback(schedule.target_id)  # type: ignore[operator]
                except Exception as exc:
                    success = False
                    error_msg = str(exc)
                    _log.warning(
                        "schedule_apply_failed",
                        schedule_id=schedule.id,
                        error=error_msg,
                    )
            schedule.record_run(success=success, error_msg=error_msg, now=now)
            await self._schedules.save(schedule)
            fired += 1
        return fired


# ===========================================================================
# N5-02  MirrorSession
# ===========================================================================


@dataclass(frozen=True, slots=True)
class CreateMirrorSessionCommand:
    name: str
    source_port_id: str
    direction: str  # MirrorDirection value
    destination_port_id: str | None = None
    destination_ip: str | None = None
    filter_vlan: int | None = None
    project_id: ProjectId | None = None
    labels: dict[str, str] = field(default_factory=dict)


class CreateMirrorSession:
    """Создать mirror-сессию (N5-02)."""

    def __init__(
        self,
        *,
        sessions: MirrorSessionRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateMirrorSessionCommand) -> MirrorSession:
        now = self._clock.now()
        session = MirrorSession(
            id=self._ids.mirror_session(),
            name=cmd.name,
            source_port_id=LogicalPortId(cmd.source_port_id),
            direction=MirrorDirection(cmd.direction),
            destination_port_id=(
                LogicalPortId(cmd.destination_port_id)
                if cmd.destination_port_id
                else None
            ),
            destination_ip=cmd.destination_ip,
            filter_vlan=cmd.filter_vlan,
            project_id=cmd.project_id,
            labels=dict(cmd.labels),
            created_at=now,
            updated_at=now,
        )
        await self._sessions.save(session)
        await self._events.publish(
            event_type="mirror.created",
            resource_type="mirror_session",
            resource_id=session.id,
            payload={"direction": cmd.direction},
            project_id=cmd.project_id,
        )
        return session


class GetMirrorSession:
    def __init__(self, *, sessions: MirrorSessionRepository) -> None:
        self._sessions = sessions

    async def execute(self, session_id: MirrorSessionId) -> MirrorSession:
        session = await self._sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"mirror_session {session_id!r} не найдено")
        return session


class ListMirrorSessions:
    def __init__(self, *, sessions: MirrorSessionRepository) -> None:
        self._sessions = sessions

    async def execute(
        self, *, project_id: ProjectId | None = None
    ) -> list[MirrorSession]:
        return await self._sessions.list(project_id=project_id)


class DeleteMirrorSession:
    def __init__(
        self, *, sessions: MirrorSessionRepository, events: EventPublisher
    ) -> None:
        self._sessions = sessions
        self._events = events

    async def execute(self, session_id: MirrorSessionId) -> None:
        session = await self._sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"mirror_session {session_id!r} не найдено")
        await self._sessions.delete(session_id)
        await self._events.publish(
            event_type="mirror.deleted",
            resource_type="mirror_session",
            resource_id=session_id,
            payload={},
            project_id=session.project_id,
        )


class ApplyMirrorSession:
    """Сгенерировать OVS-конфиг и сохранить в сущности (N5-02)."""

    def __init__(
        self,
        *,
        sessions: MirrorSessionRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._events = events
        self._configurator = MirrorConfigurator()

    async def execute(self, session_id: MirrorSessionId) -> MirrorSession:
        session = await self._sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"mirror_session {session_id!r} не найдено")
        now = self._clock.now()
        config = self._configurator.generate_config(session)
        session.apply(config, now)
        await self._sessions.save(session)
        await self._events.publish(
            event_type="mirror.applied",
            resource_type="mirror_session",
            resource_id=session.id,
            payload={},
            project_id=session.project_id,
        )
        return session


# ===========================================================================
# N5-05  VPNaaS
# ===========================================================================


@dataclass(frozen=True, slots=True)
class CreateVpnTunnelCommand:
    name: str
    protocol: str  # VpnProtocol value
    local_endpoint: str
    remote_endpoint: str
    local_public_key: str
    remote_public_key: str
    listen_port: int = 51820
    preshared_key: str | None = None
    project_id: ProjectId | None = None
    labels: dict[str, str] = field(default_factory=dict)


class CreateVpnTunnel:
    """Создать VPN-туннель (N5-05)."""

    def __init__(
        self,
        *,
        tunnels: VpnTunnelRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._tunnels = tunnels
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateVpnTunnelCommand) -> VpnTunnel:
        now = self._clock.now()
        tunnel = VpnTunnel(
            id=self._ids.vpn_tunnel(),
            name=cmd.name,
            protocol=VpnProtocol(cmd.protocol),
            local_endpoint=cmd.local_endpoint,
            remote_endpoint=cmd.remote_endpoint,
            local_public_key=cmd.local_public_key,
            remote_public_key=cmd.remote_public_key,
            listen_port=cmd.listen_port,
            preshared_key=cmd.preshared_key,
            project_id=cmd.project_id,
            labels=dict(cmd.labels),
            created_at=now,
            updated_at=now,
        )
        await self._tunnels.save(tunnel)
        await self._events.publish(
            event_type="vpn_tunnel.created",
            resource_type="vpn_tunnel",
            resource_id=tunnel.id,
            payload={"protocol": cmd.protocol},
            project_id=cmd.project_id,
        )
        return tunnel


class GetVpnTunnel:
    def __init__(self, *, tunnels: VpnTunnelRepository) -> None:
        self._tunnels = tunnels

    async def execute(self, tunnel_id: VpnTunnelId) -> VpnTunnel:
        tunnel = await self._tunnels.get(tunnel_id)
        if tunnel is None:
            raise NotFoundError(f"vpn_tunnel {tunnel_id!r} не найдено")
        return tunnel


class ListVpnTunnels:
    def __init__(self, *, tunnels: VpnTunnelRepository) -> None:
        self._tunnels = tunnels

    async def execute(
        self, *, project_id: ProjectId | None = None
    ) -> list[VpnTunnel]:
        return await self._tunnels.list(project_id=project_id)


@dataclass(frozen=True, slots=True)
class UpdateVpnTunnelCommand:
    tunnel_id: VpnTunnelId
    name: str | None = None
    remote_public_key: str | None = None
    preshared_key: str | None = None
    listen_port: int | None = None
    labels: dict[str, str] | None = None


class UpdateVpnTunnel:
    def __init__(
        self,
        *,
        tunnels: VpnTunnelRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._tunnels = tunnels
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateVpnTunnelCommand) -> VpnTunnel:
        tunnel = await self._tunnels.get(cmd.tunnel_id)
        if tunnel is None:
            raise NotFoundError(f"vpn_tunnel {cmd.tunnel_id!r} не найдено")
        now = self._clock.now()
        if cmd.name is not None:
            tunnel.name = cmd.name
        if cmd.remote_public_key is not None:
            tunnel.remote_public_key = cmd.remote_public_key
        if cmd.preshared_key is not None:
            tunnel.preshared_key = cmd.preshared_key
        if cmd.listen_port is not None:
            tunnel.listen_port = cmd.listen_port
        if cmd.labels is not None:
            tunnel.labels = dict(cmd.labels)
        tunnel.updated_at = now
        await self._tunnels.save(tunnel)
        return tunnel


class DeleteVpnTunnel:
    def __init__(
        self,
        *,
        tunnels: VpnTunnelRepository,
        peers: VpnPeerRepository,
        events: EventPublisher,
    ) -> None:
        self._tunnels = tunnels
        self._peers = peers
        self._events = events

    async def execute(self, tunnel_id: VpnTunnelId) -> None:
        tunnel = await self._tunnels.get(tunnel_id)
        if tunnel is None:
            raise NotFoundError(f"vpn_tunnel {tunnel_id!r} не найдено")
        # удаляем все peers туннеля
        peers = await self._peers.list(tunnel_id=tunnel_id)
        for peer in peers:
            await self._peers.delete(peer.id)
        await self._tunnels.delete(tunnel_id)
        await self._events.publish(
            event_type="vpn_tunnel.deleted",
            resource_type="vpn_tunnel",
            resource_id=tunnel_id,
            payload={},
            project_id=tunnel.project_id,
        )


class ApplyVpnTunnel:
    """Сгенерировать конфиг (wg0.conf / ipsec.conf) и сохранить (N5-05)."""

    def __init__(
        self,
        *,
        tunnels: VpnTunnelRepository,
        peers: VpnPeerRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._tunnels = tunnels
        self._peers = peers
        self._clock = clock
        self._events = events
        self._configurator = VpnConfigurator()

    async def execute(self, tunnel_id: VpnTunnelId) -> VpnTunnel:
        tunnel = await self._tunnels.get(tunnel_id)
        if tunnel is None:
            raise NotFoundError(f"vpn_tunnel {tunnel_id!r} не найдено")
        peers = await self._peers.list(tunnel_id=tunnel_id)
        now = self._clock.now()
        config = self._configurator.generate_config(tunnel, peers)
        tunnel.apply(config, now)
        await self._tunnels.save(tunnel)
        await self._events.publish(
            event_type="vpn_tunnel.applied",
            resource_type="vpn_tunnel",
            resource_id=tunnel.id,
            payload={"protocol": tunnel.protocol.value},
            project_id=tunnel.project_id,
        )
        return tunnel


# ------------------------------------------------------------------
# VpnPeer
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AddVpnPeerCommand:
    tunnel_id: str
    public_key: str
    allowed_ips: list[str] = field(default_factory=list)
    endpoint: str | None = None
    persistent_keepalive: int = 0


class AddVpnPeer:
    """Добавить peer в туннель (N5-05)."""

    def __init__(
        self,
        *,
        tunnels: VpnTunnelRepository,
        peers: VpnPeerRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._tunnels = tunnels
        self._peers = peers
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: AddVpnPeerCommand) -> VpnPeer:
        tunnel_id = VpnTunnelId(cmd.tunnel_id)
        tunnel = await self._tunnels.get(tunnel_id)
        if tunnel is None:
            raise NotFoundError(f"vpn_tunnel {tunnel_id!r} не найдено")
        # Проверка уникальности public_key в рамках туннеля
        existing = await self._peers.list(tunnel_id=tunnel_id)
        for p in existing:
            if p.public_key == cmd.public_key:
                raise ConflictError(
                    f"peer с public_key {cmd.public_key!r} уже существует "
                    f"в туннеле {tunnel_id!r}"
                )
        now = self._clock.now()
        peer = VpnPeer(
            id=self._ids.vpn_peer(),
            tunnel_id=tunnel_id,
            public_key=cmd.public_key,
            allowed_ips=list(cmd.allowed_ips),
            endpoint=cmd.endpoint,
            persistent_keepalive=cmd.persistent_keepalive,
            created_at=now,
            updated_at=now,
        )
        await self._peers.save(peer)
        await self._events.publish(
            event_type="vpn_peer.added",
            resource_type="vpn_peer",
            resource_id=peer.id,
            payload={"tunnel_id": tunnel_id},
            project_id=tunnel.project_id,
        )
        return peer


class GetVpnPeer:
    def __init__(self, *, peers: VpnPeerRepository) -> None:
        self._peers = peers

    async def execute(self, peer_id: VpnPeerId) -> VpnPeer:
        peer = await self._peers.get(peer_id)
        if peer is None:
            raise NotFoundError(f"vpn_peer {peer_id!r} не найдено")
        return peer


class ListVpnPeers:
    def __init__(self, *, peers: VpnPeerRepository) -> None:
        self._peers = peers

    async def execute(
        self, *, tunnel_id: VpnTunnelId | None = None
    ) -> list[VpnPeer]:
        return await self._peers.list(tunnel_id=tunnel_id)


@dataclass(frozen=True, slots=True)
class UpdateVpnPeerCommand:
    peer_id: VpnPeerId
    allowed_ips: list[str] | None = None
    endpoint: str | None = None
    persistent_keepalive: int | None = None


class UpdateVpnPeer:
    def __init__(
        self,
        *,
        peers: VpnPeerRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._peers = peers
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateVpnPeerCommand) -> VpnPeer:
        peer = await self._peers.get(cmd.peer_id)
        if peer is None:
            raise NotFoundError(f"vpn_peer {cmd.peer_id!r} не найдено")
        now = self._clock.now()
        if cmd.allowed_ips is not None:
            peer.allowed_ips = list(cmd.allowed_ips)
        if cmd.endpoint is not None:
            peer.endpoint = cmd.endpoint
        if cmd.persistent_keepalive is not None:
            peer.persistent_keepalive = cmd.persistent_keepalive
        peer.updated_at = now
        await self._peers.save(peer)
        return peer


class RemoveVpnPeer:
    def __init__(
        self, *, peers: VpnPeerRepository, events: EventPublisher
    ) -> None:
        self._peers = peers
        self._events = events

    async def execute(self, peer_id: VpnPeerId) -> None:
        peer = await self._peers.get(peer_id)
        if peer is None:
            raise NotFoundError(f"vpn_peer {peer_id!r} не найдено")
        await self._peers.delete(peer_id)
        await self._events.publish(
            event_type="vpn_peer.removed",
            resource_type="vpn_peer",
            resource_id=peer_id,
            payload={"tunnel_id": peer.tunnel_id},
        )
