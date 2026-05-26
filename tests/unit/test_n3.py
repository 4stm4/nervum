"""Unit-тесты N3 — Router, FloatingIP, BgpPeer.

N3-01  Router entity + статические маршруты + внутренние сети
N3-02  FloatingIP lifecycle (allocate / associate / disassociate / release)
N3-03  ApplyRouter — генерация конфига (RouterConfigurator)
N3-04  IPv6Config на маршрутизаторе
N3-05  BgpPeer CRUD
N3-06  HA Router (VRRP параметры на Router)
N3-07  Outbox-события
"""

from __future__ import annotations

import pytest

from sdn_controller.adapters.memory import (
    InMemoryBgpPeerRepository,
    InMemoryFloatingIpRepository,
    InMemoryOutboxRepository,
    InMemoryRouterRepository,
)
from sdn_controller.core.entities.router import IPv6Config, Router, StaticRoute
from sdn_controller.core.services.bgp_configurator import BgpConfigurator
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.services.ha_configurator import HaConfigurator
from sdn_controller.core.services.router_configurator import RouterConfigurator
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
from sdn_controller.core.value_objects.enums import (
    BgpPeerState,
    FloatingIpStatus,
    HaMode,
    Ipv6Mode,
    RouterStatus,
)
from sdn_controller.core.value_objects.errors import NotFoundError, ValidationError
from sdn_controller.core.value_objects.ids import NetworkId, ProjectId, RouterId
from tests.conftest import CountingIdFactory, FrozenClock


# ---------------------------------------------------------------------------
# Фабрики
# ---------------------------------------------------------------------------


def _make_deps() -> tuple[
    InMemoryRouterRepository,
    InMemoryFloatingIpRepository,
    InMemoryBgpPeerRepository,
    EventPublisher,
    FrozenClock,
    CountingIdFactory,
]:
    outbox = InMemoryOutboxRepository()
    clock = FrozenClock()
    ids = CountingIdFactory()
    events = EventPublisher(outbox=outbox, clock=clock, ids=ids)
    return (
        InMemoryRouterRepository(),
        InMemoryFloatingIpRepository(),
        InMemoryBgpPeerRepository(),
        events,
        clock,
        ids,
    )


# ---------------------------------------------------------------------------
# N3-01  Router entity
# ---------------------------------------------------------------------------


class TestStaticRoute:
    def test_invalid_destination_raises(self) -> None:
        with pytest.raises(ValidationError, match="CIDR"):
            StaticRoute(destination="not-cidr", nexthop="10.0.0.1")

    def test_invalid_nexthop_raises(self) -> None:
        with pytest.raises(ValidationError, match="nexthop"):
            StaticRoute(destination="10.0.0.0/8", nexthop="not-ip")

    def test_valid_route(self) -> None:
        r = StaticRoute(destination="192.168.1.0/24", nexthop="10.0.0.1")
        assert r.destination == "192.168.1.0/24"
        assert r.nexthop == "10.0.0.1"


class TestCreateRouter:
    @pytest.mark.anyio
    async def test_create_sets_build_status(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        uc = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await uc.execute(
            CreateRouterCommand(name="gw", project_id=ProjectId("proj_1"))
        )
        assert router.status == RouterStatus.BUILD
        assert router.admin_state_up is True
        assert router.ha_mode == HaMode.NONE

    @pytest.mark.anyio
    async def test_create_persists(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        uc = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await uc.execute(CreateRouterCommand(name="gw"))
        fetched = await routers.get(router.id)
        assert fetched is not None
        assert fetched.name == "gw"

    @pytest.mark.anyio
    async def test_create_emits_event(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        outbox: InMemoryOutboxRepository = events._outbox  # type: ignore[attr-defined]
        uc = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        await uc.execute(CreateRouterCommand(name="gw"))
        evts = await outbox.list_since()
        assert any(e.event_type == "router.created" for e in evts)


class TestUpdateRouter:
    @pytest.mark.anyio
    async def test_update_name(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create.execute(CreateRouterCommand(name="old"))
        uc = UpdateRouter(routers=routers, clock=clock, events=events)
        updated = await uc.execute(
            UpdateRouterCommand(router_id=router.id, name="new")
        )
        assert updated.name == "new"

    @pytest.mark.anyio
    async def test_update_ipv6(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create.execute(CreateRouterCommand(name="gw"))
        uc = UpdateRouter(routers=routers, clock=clock, events=events)
        updated = await uc.execute(
            UpdateRouterCommand(
                router_id=router.id,
                ipv6_mode="slaac",
                ipv6_prefix="fd00::/64",
            )
        )
        assert updated.ipv6_config is not None
        assert updated.ipv6_config.mode == Ipv6Mode.SLAAC
        assert updated.ipv6_config.prefix == "fd00::/64"

    @pytest.mark.anyio
    async def test_update_not_found(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        uc = UpdateRouter(routers=routers, clock=clock, events=events)
        with pytest.raises(NotFoundError):
            await uc.execute(UpdateRouterCommand(router_id=RouterId("rtr_999")))


class TestDeleteRouter:
    @pytest.mark.anyio
    async def test_delete_removes(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create.execute(CreateRouterCommand(name="gw"))
        uc = DeleteRouter(routers=routers, events=events)
        await uc.execute(router.id)
        assert await routers.get(router.id) is None

    @pytest.mark.anyio
    async def test_delete_not_found(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        uc = DeleteRouter(routers=routers, events=events)
        with pytest.raises(NotFoundError):
            await uc.execute(RouterId("rtr_999"))


class TestStaticRouteUseCase:
    @pytest.mark.anyio
    async def test_add_and_remove_route(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create.execute(CreateRouterCommand(name="gw"))

        add = AddStaticRoute(routers=routers, clock=clock, events=events)
        updated = await add.execute(
            router.id, destination="10.0.0.0/8", nexthop="192.168.1.1"
        )
        assert len(updated.static_routes) == 1

        remove = RemoveStaticRoute(routers=routers, clock=clock, events=events)
        updated2 = await remove.execute(router.id, destination="10.0.0.0/8")
        assert len(updated2.static_routes) == 0

    @pytest.mark.anyio
    async def test_add_duplicate_destination_raises(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create.execute(CreateRouterCommand(name="gw"))
        add = AddStaticRoute(routers=routers, clock=clock, events=events)
        await add.execute(router.id, destination="10.0.0.0/8", nexthop="192.168.1.1")
        with pytest.raises(ValidationError):
            await add.execute(router.id, destination="10.0.0.0/8", nexthop="192.168.1.2")


class TestInternalNetworkUseCase:
    @pytest.mark.anyio
    async def test_add_and_remove_network(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create.execute(CreateRouterCommand(name="gw"))

        add = AddInternalNetwork(routers=routers, clock=clock, events=events)
        updated = await add.execute(router.id, network_id=NetworkId("net_1"))
        assert NetworkId("net_1") in updated.internal_network_ids

        remove = RemoveInternalNetwork(routers=routers, clock=clock, events=events)
        updated2 = await remove.execute(router.id, network_id=NetworkId("net_1"))
        assert NetworkId("net_1") not in updated2.internal_network_ids


class TestSetRouterAdminState:
    @pytest.mark.anyio
    async def test_disable_sets_down(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create.execute(CreateRouterCommand(name="gw"))
        uc = SetRouterAdminState(routers=routers, clock=clock, events=events)
        updated = await uc.execute(router.id, up=False)
        assert updated.admin_state_up is False
        assert updated.status == RouterStatus.DOWN


class TestListRouters:
    @pytest.mark.anyio
    async def test_list_by_project(self) -> None:
        routers, _, _, events, clock, ids = _make_deps()
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        await create.execute(CreateRouterCommand(name="a", project_id=ProjectId("proj_1")))
        await create.execute(CreateRouterCommand(name="b", project_id=ProjectId("proj_2")))
        uc = ListRouters(routers=routers)
        result = await uc.execute(project_id=ProjectId("proj_1"))
        assert len(result) == 1
        assert result[0].name == "a"


# ---------------------------------------------------------------------------
# N3-03  ApplyRouter
# ---------------------------------------------------------------------------


class TestApplyRouter:
    @pytest.mark.anyio
    async def test_apply_sets_active_and_config(self) -> None:
        routers, _, bgp_peers, events, clock, ids = _make_deps()
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create.execute(CreateRouterCommand(name="gw"))
        uc = ApplyRouter(routers=routers, bgp_peers=bgp_peers, clock=clock, events=events)
        applied = await uc.execute(router.id)
        assert applied.status == RouterStatus.ACTIVE
        assert applied.applied_config is not None
        assert applied.applied_at is not None

    @pytest.mark.anyio
    async def test_apply_config_contains_routes(self) -> None:
        routers, _, bgp_peers, events, clock, ids = _make_deps()
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create.execute(
            CreateRouterCommand(name="gw", external_network_id=NetworkId("net_ext"))
        )
        add_route = AddStaticRoute(routers=routers, clock=clock, events=events)
        await add_route.execute(router.id, destination="10.0.0.0/8", nexthop="192.168.1.1")
        uc = ApplyRouter(routers=routers, bgp_peers=bgp_peers, clock=clock, events=events)
        applied = await uc.execute(router.id)
        assert "ip route replace 10.0.0.0/8 via 192.168.1.1" in (applied.applied_config or "")

    @pytest.mark.anyio
    async def test_apply_emits_event(self) -> None:
        routers, _, bgp_peers, events, clock, ids = _make_deps()
        outbox: InMemoryOutboxRepository = events._outbox  # type: ignore[attr-defined]
        create = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create.execute(CreateRouterCommand(name="gw"))
        uc = ApplyRouter(routers=routers, bgp_peers=bgp_peers, clock=clock, events=events)
        await uc.execute(router.id)
        evts = await outbox.list_since()
        assert any(e.event_type == "router.applied" for e in evts)


# ---------------------------------------------------------------------------
# N3-04  RouterConfigurator / IPv6
# ---------------------------------------------------------------------------


class TestRouterConfigurator:
    def test_generate_minimal(self) -> None:
        clock = FrozenClock()
        router = Router(
            id=RouterId("rtr_1"),
            name="gw",
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        cfg = RouterConfigurator().generate(router, now=clock.now())
        assert "#!/bin/sh" in cfg

    def test_generate_with_route(self) -> None:
        clock = FrozenClock()
        router = Router(
            id=RouterId("rtr_1"),
            name="gw",
            static_routes=(StaticRoute(destination="0.0.0.0/0", nexthop="10.0.0.1"),),
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        cfg = RouterConfigurator().generate(router, now=clock.now())
        assert "ip route replace 0.0.0.0/0 via 10.0.0.1" in cfg

    def test_generate_snat_for_external_network(self) -> None:
        clock = FrozenClock()
        router = Router(
            id=RouterId("rtr_1"),
            name="gw",
            external_network_id=NetworkId("net_ext"),
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        cfg = RouterConfigurator().generate(router, now=clock.now())
        assert "masquerade" in cfg


class TestBgpConfigurator:
    def test_generate_empty(self) -> None:
        clock = FrozenClock()
        router = Router(
            id=RouterId("rtr_1"),
            name="gw",
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        cfg = BgpConfigurator().generate(router, [], now=clock.now())
        assert "router id" in cfg

    def test_generate_with_peer(self) -> None:
        from datetime import UTC, datetime
        from sdn_controller.core.entities.bgp_peer import BgpPeer
        clock = FrozenClock()
        router = Router(
            id=RouterId("rtr_1"),
            name="gw",
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        peer = BgpPeer(
            id="bgpp_1",
            router_id=RouterId("rtr_1"),
            peer_ip="192.168.1.2",
            peer_asn=65001,
            local_asn=65000,
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        cfg = BgpConfigurator().generate(router, [peer], now=clock.now())
        assert "bgp" in cfg.lower()
        assert "192.168.1.2" in cfg


class TestHaConfigurator:
    def test_vrrp_not_none_raises_for_none_mode(self) -> None:
        clock = FrozenClock()
        router = Router(
            id=RouterId("rtr_1"),
            name="gw",
            ha_mode=HaMode.NONE,
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        with pytest.raises(ValidationError):
            HaConfigurator().generate(router, now=clock.now())

    def test_vrrp_config_generated(self) -> None:
        clock = FrozenClock()
        router = Router(
            id=RouterId("rtr_1"),
            name="gw",
            ha_mode=HaMode.VRRP,
            vrrp_priority=100,
            vrrp_vrid=10,
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        cfg = HaConfigurator().generate(router, virtual_ip="10.0.0.1", now=clock.now())
        assert "vrrp_instance" in cfg
        assert "priority 100" in cfg
        assert "virtual_router_id 10" in cfg


# ---------------------------------------------------------------------------
# N3-02  FloatingIP lifecycle
# ---------------------------------------------------------------------------


class TestAllocateFloatingIp:
    @pytest.mark.anyio
    async def test_allocate_creates_down_fip(self) -> None:
        _, fips, _, events, clock, ids = _make_deps()
        uc = AllocateFloatingIp(fips=fips, clock=clock, ids=ids, events=events)
        fip = await uc.execute(
            AllocateFloatingIpCommand(
                external_network_id=NetworkId("net_ext"),
                floating_ip_address="1.2.3.4",
            )
        )
        assert fip.status == FloatingIpStatus.DOWN
        assert fip.fixed_ip_address is None
        assert fip.logical_port_id is None

    @pytest.mark.anyio
    async def test_allocate_emits_event(self) -> None:
        _, fips, _, events, clock, ids = _make_deps()
        outbox: InMemoryOutboxRepository = events._outbox  # type: ignore[attr-defined]
        uc = AllocateFloatingIp(fips=fips, clock=clock, ids=ids, events=events)
        await uc.execute(
            AllocateFloatingIpCommand(
                external_network_id=NetworkId("net_ext"),
                floating_ip_address="1.2.3.4",
            )
        )
        evts = await outbox.list_since()
        assert any(e.event_type == "floating_ip.allocated" for e in evts)


class TestAssociateDisassociateFloatingIp:
    @pytest.mark.anyio
    async def test_associate_sets_active(self) -> None:
        routers, fips, _, events, clock, ids = _make_deps()
        # создаём маршрутизатор
        create_r = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create_r.execute(CreateRouterCommand(name="gw"))
        # выделяем FIP
        alloc = AllocateFloatingIp(fips=fips, clock=clock, ids=ids, events=events)
        fip = await alloc.execute(
            AllocateFloatingIpCommand(
                external_network_id=NetworkId("net_ext"),
                floating_ip_address="1.2.3.4",
            )
        )
        # ассоциируем
        uc = AssociateFloatingIp(
            fips=fips, routers=routers, clock=clock, events=events
        )
        assoc = await uc.execute(
            AssociateFloatingIpCommand(
                fip_id=fip.id,
                logical_port_id="lport_1",  # type: ignore[arg-type]
                fixed_ip_address="192.168.1.10",
                router_id=router.id,
            )
        )
        assert assoc.status == FloatingIpStatus.ACTIVE
        assert assoc.fixed_ip_address == "192.168.1.10"
        assert assoc.router_id == router.id

    @pytest.mark.anyio
    async def test_disassociate_sets_down(self) -> None:
        routers, fips, _, events, clock, ids = _make_deps()
        create_r = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create_r.execute(CreateRouterCommand(name="gw"))
        alloc = AllocateFloatingIp(fips=fips, clock=clock, ids=ids, events=events)
        fip = await alloc.execute(
            AllocateFloatingIpCommand(
                external_network_id=NetworkId("net_ext"),
                floating_ip_address="1.2.3.4",
            )
        )
        assoc_uc = AssociateFloatingIp(
            fips=fips, routers=routers, clock=clock, events=events
        )
        fip = await assoc_uc.execute(
            AssociateFloatingIpCommand(
                fip_id=fip.id,
                logical_port_id="lport_1",  # type: ignore[arg-type]
                fixed_ip_address="192.168.1.10",
                router_id=router.id,
            )
        )
        disassoc_uc = DisassociateFloatingIp(fips=fips, clock=clock, events=events)
        result = await disassoc_uc.execute(fip.id)
        assert result.status == FloatingIpStatus.DOWN
        assert result.fixed_ip_address is None

    @pytest.mark.anyio
    async def test_disassociate_not_active_raises(self) -> None:
        _, fips, _, events, clock, ids = _make_deps()
        alloc = AllocateFloatingIp(fips=fips, clock=clock, ids=ids, events=events)
        fip = await alloc.execute(
            AllocateFloatingIpCommand(
                external_network_id=NetworkId("net_ext"),
                floating_ip_address="1.2.3.4",
            )
        )
        uc = DisassociateFloatingIp(fips=fips, clock=clock, events=events)
        with pytest.raises(ValidationError):
            await uc.execute(fip.id)

    @pytest.mark.anyio
    async def test_associate_nonexistent_router_raises(self) -> None:
        routers, fips, _, events, clock, ids = _make_deps()
        alloc = AllocateFloatingIp(fips=fips, clock=clock, ids=ids, events=events)
        fip = await alloc.execute(
            AllocateFloatingIpCommand(
                external_network_id=NetworkId("net_ext"),
                floating_ip_address="1.2.3.4",
            )
        )
        uc = AssociateFloatingIp(fips=fips, routers=routers, clock=clock, events=events)
        with pytest.raises(NotFoundError):
            await uc.execute(
                AssociateFloatingIpCommand(
                    fip_id=fip.id,
                    logical_port_id="lport_1",  # type: ignore[arg-type]
                    fixed_ip_address="192.168.1.10",
                    router_id=RouterId("rtr_999"),
                )
            )


class TestReleaseFloatingIp:
    @pytest.mark.anyio
    async def test_release_deletes(self) -> None:
        _, fips, _, events, clock, ids = _make_deps()
        alloc = AllocateFloatingIp(fips=fips, clock=clock, ids=ids, events=events)
        fip = await alloc.execute(
            AllocateFloatingIpCommand(
                external_network_id=NetworkId("net_ext"),
                floating_ip_address="1.2.3.4",
            )
        )
        uc = ReleaseFloatingIp(fips=fips, events=events)
        await uc.execute(fip.id)
        assert await fips.get(fip.id) is None


class TestListFloatingIps:
    @pytest.mark.anyio
    async def test_list_by_project(self) -> None:
        _, fips, _, events, clock, ids = _make_deps()
        alloc = AllocateFloatingIp(fips=fips, clock=clock, ids=ids, events=events)
        await alloc.execute(
            AllocateFloatingIpCommand(
                external_network_id=NetworkId("net_ext"),
                floating_ip_address="1.2.3.4",
                project_id=ProjectId("proj_1"),
            )
        )
        await alloc.execute(
            AllocateFloatingIpCommand(
                external_network_id=NetworkId("net_ext"),
                floating_ip_address="1.2.3.5",
                project_id=ProjectId("proj_2"),
            )
        )
        uc = ListFloatingIps(fips=fips)
        result = await uc.execute(project_id=ProjectId("proj_1"))
        assert len(result) == 1
        assert result[0].floating_ip_address == "1.2.3.4"


# ---------------------------------------------------------------------------
# N3-05  BgpPeer
# ---------------------------------------------------------------------------


class TestCreateBgpPeer:
    @pytest.mark.anyio
    async def test_create_peer(self) -> None:
        routers, _, bgp_peers, events, clock, ids = _make_deps()
        create_r = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create_r.execute(CreateRouterCommand(name="gw"))
        uc = CreateBgpPeer(
            bgp_peers=bgp_peers, routers=routers, clock=clock, ids=ids, events=events
        )
        peer = await uc.execute(
            CreateBgpPeerCommand(
                router_id=router.id,
                peer_ip="192.168.1.2",
                peer_asn=65001,
                local_asn=65000,
            )
        )
        assert peer.peer_ip == "192.168.1.2"
        assert peer.peer_asn == 65001
        assert peer.state == BgpPeerState.IDLE

    @pytest.mark.anyio
    async def test_create_peer_nonexistent_router_raises(self) -> None:
        routers, _, bgp_peers, events, clock, ids = _make_deps()
        uc = CreateBgpPeer(
            bgp_peers=bgp_peers, routers=routers, clock=clock, ids=ids, events=events
        )
        with pytest.raises(NotFoundError):
            await uc.execute(
                CreateBgpPeerCommand(
                    router_id=RouterId("rtr_999"),
                    peer_ip="192.168.1.2",
                    peer_asn=65001,
                    local_asn=65000,
                )
            )

    def test_create_peer_invalid_asn_raises(self) -> None:
        from sdn_controller.core.entities.bgp_peer import BgpPeer
        clock = FrozenClock()
        with pytest.raises(ValidationError, match="peer_asn"):
            BgpPeer(
                id="bgpp_1",
                router_id=RouterId("rtr_1"),
                peer_ip="192.168.1.2",
                peer_asn=0,
                local_asn=65000,
                created_at=clock.now(),
                updated_at=clock.now(),
            )


class TestUpdateBgpPeerState:
    @pytest.mark.anyio
    async def test_update_state(self) -> None:
        routers, _, bgp_peers, events, clock, ids = _make_deps()
        create_r = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create_r.execute(CreateRouterCommand(name="gw"))
        create_p = CreateBgpPeer(
            bgp_peers=bgp_peers, routers=routers, clock=clock, ids=ids, events=events
        )
        peer = await create_p.execute(
            CreateBgpPeerCommand(
                router_id=router.id,
                peer_ip="192.168.1.2",
                peer_asn=65001,
                local_asn=65000,
            )
        )
        uc = UpdateBgpPeerState(bgp_peers=bgp_peers, clock=clock)
        updated = await uc.execute(peer.id, state="established")
        assert updated.state == BgpPeerState.ESTABLISHED


class TestDeleteBgpPeer:
    @pytest.mark.anyio
    async def test_delete_peer(self) -> None:
        routers, _, bgp_peers, events, clock, ids = _make_deps()
        create_r = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        router = await create_r.execute(CreateRouterCommand(name="gw"))
        create_p = CreateBgpPeer(
            bgp_peers=bgp_peers, routers=routers, clock=clock, ids=ids, events=events
        )
        peer = await create_p.execute(
            CreateBgpPeerCommand(
                router_id=router.id,
                peer_ip="192.168.1.2",
                peer_asn=65001,
                local_asn=65000,
            )
        )
        uc = DeleteBgpPeer(bgp_peers=bgp_peers, events=events)
        await uc.execute(peer.id)
        assert await bgp_peers.get(peer.id) is None


class TestListBgpPeers:
    @pytest.mark.anyio
    async def test_list_by_router(self) -> None:
        routers, _, bgp_peers, events, clock, ids = _make_deps()
        create_r = CreateRouter(routers=routers, clock=clock, ids=ids, events=events)
        r1 = await create_r.execute(CreateRouterCommand(name="gw1"))
        r2 = await create_r.execute(CreateRouterCommand(name="gw2"))
        create_p = CreateBgpPeer(
            bgp_peers=bgp_peers, routers=routers, clock=clock, ids=ids, events=events
        )
        await create_p.execute(
            CreateBgpPeerCommand(router_id=r1.id, peer_ip="1.1.1.1", peer_asn=65001, local_asn=65000)
        )
        await create_p.execute(
            CreateBgpPeerCommand(router_id=r2.id, peer_ip="2.2.2.2", peer_asn=65002, local_asn=65000)
        )
        uc = ListBgpPeers(bgp_peers=bgp_peers)
        result = await uc.execute(router_id=r1.id)
        assert len(result) == 1
        assert result[0].peer_ip == "1.1.1.1"


# ---------------------------------------------------------------------------
# N3-06  HA Router (VRRP)
# ---------------------------------------------------------------------------


class TestHaRouter:
    def test_vrrp_invalid_priority_raises(self) -> None:
        clock = FrozenClock()
        with pytest.raises(ValidationError, match="priority"):
            Router(
                id=RouterId("rtr_1"),
                name="gw",
                ha_mode=HaMode.VRRP,
                vrrp_priority=255,
                vrrp_vrid=10,
                created_at=clock.now(),
                updated_at=clock.now(),
            )

    def test_vrrp_valid(self) -> None:
        clock = FrozenClock()
        router = Router(
            id=RouterId("rtr_1"),
            name="gw",
            ha_mode=HaMode.VRRP,
            vrrp_priority=100,
            vrrp_vrid=10,
            created_at=clock.now(),
            updated_at=clock.now(),
        )
        assert router.ha_mode == HaMode.VRRP
        assert router.vrrp_priority == 100
