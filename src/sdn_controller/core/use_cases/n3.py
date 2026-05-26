"""Use cases N3 — Router, FloatingIP, BgpPeer.

N3-01  Router: CRUD + управление маршрутами и внутренними сетями
N3-02  FloatingIP lifecycle (allocate / associate / disassociate / release)
N3-03  ApplyRouter: генерация конфига (ip route + NAT + IPv6 + BGP + HA)
N3-04  IPv6 / SLAAC / DHCPv6 — через ipv6_config на маршрутизаторе
N3-05  BGP peering — BgpPeer CRUD
N3-06  HA Router (VRRP) — ha_mode + VRRP параметры на Router
N3-07  Outbox-события для всех мутирующих операций
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sdn_controller.core.entities.bgp_peer import BgpPeer
from sdn_controller.core.entities.floating_ip import FloatingIP
from sdn_controller.core.entities.router import IPv6Config, Router, StaticRoute
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.services.router_configurator import RouterConfigurator
from sdn_controller.core.value_objects.enums import (
    BgpPeerState,
    FloatingIpStatus,
    HaMode,
    Ipv6Mode,
)
from sdn_controller.core.value_objects.errors import NotFoundError, ValidationError
from sdn_controller.core.value_objects.ids import (
    BgpPeerId,
    FloatingIpId,
    IdFactory,
    LogicalPortId,
    NetworkId,
    ProjectId,
    RouterId,
)
from sdn_controller.ports.persistence import (
    BgpPeerRepository,
    FloatingIpRepository,
    LogicalPortRepository,
    RouterRepository,
)

__all__ = [
    # Router
    "CreateRouterCommand",
    "UpdateRouterCommand",
    "CreateRouter",
    "GetRouter",
    "ListRouters",
    "UpdateRouter",
    "DeleteRouter",
    "AddStaticRoute",
    "RemoveStaticRoute",
    "AddInternalNetwork",
    "RemoveInternalNetwork",
    "ApplyRouter",
    "SetRouterAdminState",
    # FloatingIP
    "AllocateFloatingIpCommand",
    "AssociateFloatingIpCommand",
    "AllocateFloatingIp",
    "GetFloatingIp",
    "ListFloatingIps",
    "AssociateFloatingIp",
    "DisassociateFloatingIp",
    "ReleaseFloatingIp",
    # BgpPeer
    "CreateBgpPeerCommand",
    "CreateBgpPeer",
    "GetBgpPeer",
    "ListBgpPeers",
    "DeleteBgpPeer",
    "UpdateBgpPeerState",
]

_UNSET: Any = object()


# ---------------------------------------------------------------------------
# Router commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateRouterCommand:
    name: str
    description: str = ""
    project_id: ProjectId | None = None
    external_network_id: NetworkId | None = None
    ha_mode: str = "none"
    vrrp_priority: int | None = None
    vrrp_vrid: int | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class UpdateRouterCommand:
    router_id: RouterId
    name: str | None = None
    description: str | None = None
    external_network_id: Any = _UNSET
    ha_mode: str | None = None
    vrrp_priority: Any = _UNSET
    vrrp_vrid: Any = _UNSET
    ipv6_mode: str | None = None
    ipv6_prefix: str | None = None
    ipv6_dhcpv6_stateful: bool | None = None
    labels: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Router use cases (N3-01, N3-03, N3-06)
# ---------------------------------------------------------------------------


class CreateRouter:
    """Создаёт L3-маршрутизатор в статусе build (N3-01)."""

    def __init__(
        self,
        *,
        routers: RouterRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._routers = routers
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateRouterCommand) -> Router:
        now = self._clock.now()
        router = Router(
            id=self._ids.router(),
            name=cmd.name,
            description=cmd.description,
            project_id=cmd.project_id,
            external_network_id=cmd.external_network_id,
            ha_mode=HaMode(cmd.ha_mode),
            vrrp_priority=cmd.vrrp_priority,
            vrrp_vrid=cmd.vrrp_vrid,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._routers.save(router)
        await self._events.publish(
            event_type="router.created",
            resource_type="router",
            resource_id=router.id,
            payload={"name": router.name},
            project_id=router.project_id,
        )
        return router


class GetRouter:
    def __init__(self, *, routers: RouterRepository) -> None:
        self._routers = routers

    async def execute(self, router_id: RouterId) -> Router:
        router = await self._routers.get(router_id)
        if router is None:
            raise NotFoundError(f"маршрутизатор {router_id} не найден")
        return router


class ListRouters:
    def __init__(self, *, routers: RouterRepository) -> None:
        self._routers = routers

    async def execute(self, *, project_id: ProjectId | None = None) -> list[Router]:
        return await self._routers.list(project_id=project_id)


class UpdateRouter:
    """Обновляет метаданные маршрутизатора (N3-01, N3-04, N3-06)."""

    def __init__(
        self,
        *,
        routers: RouterRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._routers = routers
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateRouterCommand) -> Router:
        router = await self._routers.get(cmd.router_id)
        if router is None:
            raise NotFoundError(f"маршрутизатор {cmd.router_id} не найден")

        # Строим обновлённый IPv6Config
        ipv6_config: Any = _UNSET
        if any(
            v is not None
            for v in (cmd.ipv6_mode, cmd.ipv6_prefix, cmd.ipv6_dhcpv6_stateful)
        ):
            base = router.ipv6_config
            ipv6_config = IPv6Config(
                mode=Ipv6Mode(cmd.ipv6_mode) if cmd.ipv6_mode else (
                    base.mode if base else Ipv6Mode.OFF
                ),
                prefix=cmd.ipv6_prefix if cmd.ipv6_prefix is not None else (
                    base.prefix if base else ""
                ),
                dhcpv6_stateful=cmd.ipv6_dhcpv6_stateful if cmd.ipv6_dhcpv6_stateful is not None else (
                    base.dhcpv6_stateful if base else False
                ),
            )

        # Передаём только те поля, которые явно указаны в команде;
        # для остальных не передаём аргумент — entity использует свой _UNSET.
        update_kwargs: dict[str, Any] = {
            "name": cmd.name,
            "description": cmd.description,
            "ha_mode": HaMode(cmd.ha_mode) if cmd.ha_mode else None,
            "labels": cmd.labels,
            "now": self._clock.now(),
        }
        if cmd.external_network_id is not _UNSET:
            update_kwargs["external_network_id"] = cmd.external_network_id
        if cmd.vrrp_priority is not _UNSET:
            update_kwargs["vrrp_priority"] = cmd.vrrp_priority
        if cmd.vrrp_vrid is not _UNSET:
            update_kwargs["vrrp_vrid"] = cmd.vrrp_vrid
        if ipv6_config is not _UNSET:
            update_kwargs["ipv6_config"] = ipv6_config
        router.update(**update_kwargs)
        await self._routers.save(router)
        await self._events.publish(
            event_type="router.updated",
            resource_type="router",
            resource_id=router.id,
            project_id=router.project_id,
        )
        return router


class DeleteRouter:
    def __init__(
        self,
        *,
        routers: RouterRepository,
        events: EventPublisher,
    ) -> None:
        self._routers = routers
        self._events = events

    async def execute(self, router_id: RouterId) -> None:
        router = await self._routers.get(router_id)
        if router is None:
            raise NotFoundError(f"маршрутизатор {router_id} не найден")
        await self._routers.delete(router_id)
        await self._events.publish(
            event_type="router.deleted",
            resource_type="router",
            resource_id=router_id,
            project_id=router.project_id,
        )


class AddStaticRoute:
    """Добавляет статический маршрут к маршрутизатору (N3-01)."""

    def __init__(
        self,
        *,
        routers: RouterRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._routers = routers
        self._clock = clock
        self._events = events

    async def execute(
        self,
        router_id: RouterId,
        *,
        destination: str,
        nexthop: str,
    ) -> Router:
        router = await self._routers.get(router_id)
        if router is None:
            raise NotFoundError(f"маршрутизатор {router_id} не найден")
        route = StaticRoute(destination=destination, nexthop=nexthop)
        router.add_static_route(route, now=self._clock.now())
        await self._routers.save(router)
        await self._events.publish(
            event_type="router.route_added",
            resource_type="router",
            resource_id=router.id,
            payload={"destination": destination, "nexthop": nexthop},
            project_id=router.project_id,
        )
        return router


class RemoveStaticRoute:
    """Удаляет статический маршрут из маршрутизатора (N3-01)."""

    def __init__(
        self,
        *,
        routers: RouterRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._routers = routers
        self._clock = clock
        self._events = events

    async def execute(self, router_id: RouterId, *, destination: str) -> Router:
        router = await self._routers.get(router_id)
        if router is None:
            raise NotFoundError(f"маршрутизатор {router_id} не найден")
        router.remove_static_route(destination, now=self._clock.now())
        await self._routers.save(router)
        await self._events.publish(
            event_type="router.route_removed",
            resource_type="router",
            resource_id=router.id,
            payload={"destination": destination},
            project_id=router.project_id,
        )
        return router


class AddInternalNetwork:
    """Подключает внутреннюю сеть к маршрутизатору (N3-01)."""

    def __init__(
        self,
        *,
        routers: RouterRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._routers = routers
        self._clock = clock
        self._events = events

    async def execute(self, router_id: RouterId, *, network_id: NetworkId) -> Router:
        router = await self._routers.get(router_id)
        if router is None:
            raise NotFoundError(f"маршрутизатор {router_id} не найден")
        router.add_internal_network(network_id, now=self._clock.now())
        await self._routers.save(router)
        await self._events.publish(
            event_type="router.network_added",
            resource_type="router",
            resource_id=router.id,
            payload={"network_id": network_id},
            project_id=router.project_id,
        )
        return router


class RemoveInternalNetwork:
    """Отключает внутреннюю сеть от маршрутизатора (N3-01)."""

    def __init__(
        self,
        *,
        routers: RouterRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._routers = routers
        self._clock = clock
        self._events = events

    async def execute(self, router_id: RouterId, *, network_id: NetworkId) -> Router:
        router = await self._routers.get(router_id)
        if router is None:
            raise NotFoundError(f"маршрутизатор {router_id} не найден")
        router.remove_internal_network(network_id, now=self._clock.now())
        await self._routers.save(router)
        await self._events.publish(
            event_type="router.network_removed",
            resource_type="router",
            resource_id=router.id,
            payload={"network_id": network_id},
            project_id=router.project_id,
        )
        return router


class ApplyRouter:
    """Генерирует конфиг маршрутизатора; переводит в статус active (N3-03).

    В MVP — логический переход (конфиг генерируется, сохраняется,
    реального вызова агента нет; это N4+).
    """

    def __init__(
        self,
        *,
        routers: RouterRepository,
        bgp_peers: BgpPeerRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._routers = routers
        self._bgp_peers = bgp_peers
        self._clock = clock
        self._events = events
        self._configurator = RouterConfigurator()

    async def execute(self, router_id: RouterId) -> Router:
        router = await self._routers.get(router_id)
        if router is None:
            raise NotFoundError(f"маршрутизатор {router_id} не найден")
        peers = await self._bgp_peers.list(router_id=router_id)
        now = self._clock.now()
        config = self._configurator.generate(router, peers, now=now)
        router.mark_active(config=config, now=now)
        await self._routers.save(router)
        await self._events.publish(
            event_type="router.applied",
            resource_type="router",
            resource_id=router.id,
            project_id=router.project_id,
        )
        return router


class SetRouterAdminState:
    """Административное включение/выключение маршрутизатора."""

    def __init__(
        self,
        *,
        routers: RouterRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._routers = routers
        self._clock = clock
        self._events = events

    async def execute(self, router_id: RouterId, *, up: bool) -> Router:
        router = await self._routers.get(router_id)
        if router is None:
            raise NotFoundError(f"маршрутизатор {router_id} не найден")
        router.set_admin_state(up=up, now=self._clock.now())
        await self._routers.save(router)
        await self._events.publish(
            event_type="router.admin_state_changed",
            resource_type="router",
            resource_id=router.id,
            payload={"admin_state_up": up},
            project_id=router.project_id,
        )
        return router


# ---------------------------------------------------------------------------
# FloatingIP use cases (N3-02)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AllocateFloatingIpCommand:
    external_network_id: NetworkId
    floating_ip_address: str
    project_id: ProjectId | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class AssociateFloatingIpCommand:
    fip_id: FloatingIpId
    logical_port_id: LogicalPortId
    fixed_ip_address: str
    router_id: RouterId


class AllocateFloatingIp:
    """Выделяет Floating IP в статусе DOWN (N3-02)."""

    def __init__(
        self,
        *,
        fips: FloatingIpRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._fips = fips
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: AllocateFloatingIpCommand) -> FloatingIP:
        now = self._clock.now()
        fip = FloatingIP(
            id=self._ids.floating_ip(),
            external_network_id=cmd.external_network_id,
            floating_ip_address=cmd.floating_ip_address,
            project_id=cmd.project_id,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._fips.save(fip)
        await self._events.publish(
            event_type="floating_ip.allocated",
            resource_type="floating_ip",
            resource_id=fip.id,
            payload={"floating_ip_address": fip.floating_ip_address},
            project_id=fip.project_id,
        )
        return fip


class GetFloatingIp:
    def __init__(self, *, fips: FloatingIpRepository) -> None:
        self._fips = fips

    async def execute(self, fip_id: FloatingIpId) -> FloatingIP:
        fip = await self._fips.get(fip_id)
        if fip is None:
            raise NotFoundError(f"floating IP {fip_id} не найден")
        return fip


class ListFloatingIps:
    def __init__(self, *, fips: FloatingIpRepository) -> None:
        self._fips = fips

    async def execute(
        self,
        *,
        project_id: ProjectId | None = None,
        router_id: RouterId | None = None,
    ) -> list[FloatingIP]:
        return await self._fips.list(project_id=project_id, router_id=router_id)


class AssociateFloatingIp:
    """Ассоциирует Floating IP с логическим портом (N3-02)."""

    def __init__(
        self,
        *,
        fips: FloatingIpRepository,
        routers: RouterRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._fips = fips
        self._routers = routers
        self._clock = clock
        self._events = events

    async def execute(self, cmd: AssociateFloatingIpCommand) -> FloatingIP:
        fip = await self._fips.get(cmd.fip_id)
        if fip is None:
            raise NotFoundError(f"floating IP {cmd.fip_id} не найден")
        if await self._routers.get(cmd.router_id) is None:
            raise NotFoundError(f"маршрутизатор {cmd.router_id} не найден")
        fip.associate(
            logical_port_id=cmd.logical_port_id,
            fixed_ip_address=cmd.fixed_ip_address,
            router_id=cmd.router_id,
            now=self._clock.now(),
        )
        await self._fips.save(fip)
        await self._events.publish(
            event_type="floating_ip.associated",
            resource_type="floating_ip",
            resource_id=fip.id,
            payload={
                "floating_ip_address": fip.floating_ip_address,
                "fixed_ip_address": cmd.fixed_ip_address,
            },
            project_id=fip.project_id,
        )
        return fip


class DisassociateFloatingIp:
    """Снимает ассоциацию Floating IP; переводит в DOWN (N3-02)."""

    def __init__(
        self,
        *,
        fips: FloatingIpRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._fips = fips
        self._clock = clock
        self._events = events

    async def execute(self, fip_id: FloatingIpId) -> FloatingIP:
        fip = await self._fips.get(fip_id)
        if fip is None:
            raise NotFoundError(f"floating IP {fip_id} не найден")
        if fip.status != FloatingIpStatus.ACTIVE:
            raise ValidationError("floating IP не ассоциирован")
        fip.disassociate(now=self._clock.now())
        await self._fips.save(fip)
        await self._events.publish(
            event_type="floating_ip.disassociated",
            resource_type="floating_ip",
            resource_id=fip.id,
            project_id=fip.project_id,
        )
        return fip


class ReleaseFloatingIp:
    """Освобождает Floating IP (удаляет запись) (N3-02)."""

    def __init__(
        self,
        *,
        fips: FloatingIpRepository,
        events: EventPublisher,
    ) -> None:
        self._fips = fips
        self._events = events

    async def execute(self, fip_id: FloatingIpId) -> None:
        fip = await self._fips.get(fip_id)
        if fip is None:
            raise NotFoundError(f"floating IP {fip_id} не найден")
        await self._fips.delete(fip_id)
        await self._events.publish(
            event_type="floating_ip.released",
            resource_type="floating_ip",
            resource_id=fip_id,
            project_id=fip.project_id,
        )


# ---------------------------------------------------------------------------
# BgpPeer use cases (N3-05)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateBgpPeerCommand:
    router_id: RouterId
    peer_ip: str
    peer_asn: int
    local_asn: int
    password: str = ""
    project_id: ProjectId | None = None
    labels: dict[str, str] | None = None


class CreateBgpPeer:
    """Создаёт BGP-пир для маршрутизатора (N3-05)."""

    def __init__(
        self,
        *,
        bgp_peers: BgpPeerRepository,
        routers: RouterRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._bgp_peers = bgp_peers
        self._routers = routers
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateBgpPeerCommand) -> BgpPeer:
        if await self._routers.get(cmd.router_id) is None:
            raise NotFoundError(f"маршрутизатор {cmd.router_id} не найден")
        now = self._clock.now()
        peer = BgpPeer(
            id=self._ids.bgp_peer(),
            router_id=cmd.router_id,
            peer_ip=cmd.peer_ip,
            peer_asn=cmd.peer_asn,
            local_asn=cmd.local_asn,
            password=cmd.password,
            project_id=cmd.project_id,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._bgp_peers.save(peer)
        await self._events.publish(
            event_type="bgp_peer.created",
            resource_type="bgp_peer",
            resource_id=peer.id,
            payload={"peer_ip": peer.peer_ip, "peer_asn": peer.peer_asn},
            project_id=peer.project_id,
        )
        return peer


class GetBgpPeer:
    def __init__(self, *, bgp_peers: BgpPeerRepository) -> None:
        self._bgp_peers = bgp_peers

    async def execute(self, peer_id: BgpPeerId) -> BgpPeer:
        peer = await self._bgp_peers.get(peer_id)
        if peer is None:
            raise NotFoundError(f"BGP-пир {peer_id} не найден")
        return peer


class ListBgpPeers:
    def __init__(self, *, bgp_peers: BgpPeerRepository) -> None:
        self._bgp_peers = bgp_peers

    async def execute(
        self,
        *,
        router_id: RouterId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[BgpPeer]:
        return await self._bgp_peers.list(router_id=router_id, project_id=project_id)


class DeleteBgpPeer:
    def __init__(
        self,
        *,
        bgp_peers: BgpPeerRepository,
        events: EventPublisher,
    ) -> None:
        self._bgp_peers = bgp_peers
        self._events = events

    async def execute(self, peer_id: BgpPeerId) -> None:
        peer = await self._bgp_peers.get(peer_id)
        if peer is None:
            raise NotFoundError(f"BGP-пир {peer_id} не найден")
        await self._bgp_peers.delete(peer_id)
        await self._events.publish(
            event_type="bgp_peer.deleted",
            resource_type="bgp_peer",
            resource_id=peer_id,
            project_id=peer.project_id,
        )


class UpdateBgpPeerState:
    """Обновляет состояние BGP-сессии (вызывается верификатором агента)."""

    def __init__(
        self,
        *,
        bgp_peers: BgpPeerRepository,
        clock: Clock,
    ) -> None:
        self._bgp_peers = bgp_peers
        self._clock = clock

    async def execute(self, peer_id: BgpPeerId, *, state: str) -> BgpPeer:
        peer = await self._bgp_peers.get(peer_id)
        if peer is None:
            raise NotFoundError(f"BGP-пир {peer_id} не найден")
        peer.update_state(BgpPeerState(state), now=self._clock.now())
        await self._bgp_peers.save(peer)
        return peer
