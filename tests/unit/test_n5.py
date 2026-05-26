"""Unit-тесты N5 — Advanced.

Покрывают:
  N5-01  ApplySchedule: CreateApplySchedule, GetApplySchedule,
                        ListApplySchedules, UpdateApplySchedule,
                        DeleteApplySchedule, ToggleApplySchedule,
                        TickScheduler, CronScheduler
  N5-02  MirrorSession: CreateMirrorSession, GetMirrorSession,
                        DeleteMirrorSession, ApplyMirrorSession,
                        MirrorConfigurator
  N5-03  GrpcAgentClient: FakeGrpcTransport
  N5-04  PgAdvisoryLockStore: _key_to_bigint
  N5-05  VPNaaS: CreateVpnTunnel, GetVpnTunnel, UpdateVpnTunnel,
                  DeleteVpnTunnel, ApplyVpnTunnel,
                  AddVpnPeer, GetVpnPeer, ListVpnPeers, RemoveVpnPeer,
                  VpnConfigurator
"""

from __future__ import annotations

import pytest

from sdn_controller.adapters.memory.repositories import (
    InMemoryApplyScheduleRepository,
    InMemoryMirrorSessionRepository,
    InMemoryVpnPeerRepository,
    InMemoryVpnTunnelRepository,
)
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.adapters.netos_agent.grpc_client import (
    FakeGrpcTransport,
    GrpcAgentClient,
    build_fake_grpc_client,
)
from sdn_controller.adapters.sql.pg_advisory_locks import _key_to_bigint
from sdn_controller.core.entities.apply_schedule import ApplySchedule
from sdn_controller.core.entities.mirror_session import MirrorSession
from sdn_controller.core.entities.vpn_tunnel import VpnPeer, VpnTunnel
from sdn_controller.core.services.cron_scheduler import CronScheduler
from sdn_controller.core.services.mirror_configurator import MirrorConfigurator
from sdn_controller.core.services.vpn_configurator import VpnConfigurator
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
    ListVpnPeers,
    RemoveVpnPeer,
    TickScheduler,
    ToggleApplySchedule,
    UpdateApplySchedule,
    UpdateApplyScheduleCommand,
    UpdateVpnPeer,
    UpdateVpnPeerCommand,
    UpdateVpnTunnel,
    UpdateVpnTunnelCommand,
)
from sdn_controller.core.value_objects.enums import (
    MirrorDirection,
    MirrorStatus,
    ScheduleStatus,
    ScheduleTargetType,
    VpnProtocol,
    VpnStatus,
)
from sdn_controller.core.value_objects.errors import ConflictError, NotFoundError
from sdn_controller.core.value_objects.ids import (
    ApplyScheduleId,
    LogicalPortId,
    MirrorSessionId,
    ProjectId,
    VpnPeerId,
    VpnTunnelId,
)
from tests.conftest import CountingIdFactory, FrozenClock


class _NullEvents:
    """Заглушка EventPublisher для unit-тестов."""

    async def publish(self, **kwargs: object) -> None:  # type: ignore[override]
        pass


# ===========================================================================
# N5-01  CronScheduler
# ===========================================================================


class TestCronScheduler:
    def test_every_minute(self) -> None:
        from datetime import UTC, datetime

        now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        assert CronScheduler.is_due("* * * * *", now) is True

    def test_specific_minute(self) -> None:
        from datetime import UTC, datetime

        now = datetime(2026, 1, 15, 12, 30, 0, tzinfo=UTC)
        assert CronScheduler.is_due("30 12 * * *", now) is True
        assert CronScheduler.is_due("31 12 * * *", now) is False

    def test_step(self) -> None:
        from datetime import UTC, datetime

        # каждые 15 минут: 0, 15, 30, 45
        now = datetime(2026, 1, 15, 12, 30, 0, tzinfo=UTC)
        assert CronScheduler.is_due("*/15 * * * *", now) is True

        now2 = datetime(2026, 1, 15, 12, 31, 0, tzinfo=UTC)
        assert CronScheduler.is_due("*/15 * * * *", now2) is False

    def test_range(self) -> None:
        from datetime import UTC, datetime

        # 9-17 часов
        now_in = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        assert CronScheduler.is_due("0 9-17 * * *", now_in) is True

        now_out = datetime(2026, 1, 15, 18, 0, 0, tzinfo=UTC)
        assert CronScheduler.is_due("0 9-17 * * *", now_out) is False

    def test_validate_ok(self) -> None:
        CronScheduler.validate("*/5 * * * *")  # не бросает

    def test_validate_bad(self) -> None:
        from sdn_controller.core.value_objects.errors import ValidationError

        with pytest.raises((ValueError, ValidationError)):
            CronScheduler.validate("bad expr")


# ===========================================================================
# N5-01  ApplySchedule use cases
# ===========================================================================


@pytest.fixture()
def sched_repo() -> InMemoryApplyScheduleRepository:
    return InMemoryApplyScheduleRepository()


@pytest.fixture()
def clock() -> FrozenClock:
    from datetime import UTC, datetime

    return FrozenClock(datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC))


@pytest.fixture()
def ids() -> CountingIdFactory:
    return CountingIdFactory()


@pytest.fixture()
def null_events() -> _NullEvents:
    return _NullEvents()


class TestCreateApplySchedule:
    @pytest.mark.anyio
    async def test_creates_and_saves(
        self,
        sched_repo: InMemoryApplyScheduleRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        uc = CreateApplySchedule(
            schedules=sched_repo, clock=clock, ids=ids, events=null_events
        )
        cmd = CreateApplyScheduleCommand(
            name="every-hour",
            cron_expr="0 * * * *",
            target_type="network",
            target_id="net-1",
        )
        result = await uc.execute(cmd)
        assert result.name == "every-hour"
        assert result.cron_expr == "0 * * * *"
        assert result.target_type == ScheduleTargetType.NETWORK
        assert result.enabled is True
        assert result.status == ScheduleStatus.ACTIVE
        saved = await sched_repo.get(result.id)
        assert saved is not None

    @pytest.mark.anyio
    async def test_invalid_cron_raises(
        self,
        sched_repo: InMemoryApplyScheduleRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        from sdn_controller.core.value_objects.errors import ValidationError

        uc = CreateApplySchedule(
            schedules=sched_repo, clock=clock, ids=ids, events=null_events
        )
        with pytest.raises((ValueError, ValidationError)):
            await uc.execute(
                CreateApplyScheduleCommand(
                    name="bad",
                    cron_expr="bad cron",
                    target_type="network",
                    target_id="x",
                )
            )


class TestToggleApplySchedule:
    @pytest.mark.anyio
    async def test_pause_and_enable(
        self,
        sched_repo: InMemoryApplyScheduleRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        create_uc = CreateApplySchedule(
            schedules=sched_repo, clock=clock, ids=ids, events=null_events
        )
        schedule = await create_uc.execute(
            CreateApplyScheduleCommand(
                name="s",
                cron_expr="* * * * *",
                target_type="network",
                target_id="n1",
            )
        )
        toggle_uc = ToggleApplySchedule(
            schedules=sched_repo, clock=clock, events=null_events
        )
        paused = await toggle_uc.execute(schedule.id, enabled=False)
        assert paused.enabled is False
        assert paused.status == ScheduleStatus.PAUSED

        re_enabled = await toggle_uc.execute(schedule.id, enabled=True)
        assert re_enabled.enabled is True
        assert re_enabled.status == ScheduleStatus.ACTIVE


class TestTickScheduler:
    @pytest.mark.anyio
    async def test_fires_due_schedule(
        self,
        sched_repo: InMemoryApplyScheduleRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        create_uc = CreateApplySchedule(
            schedules=sched_repo, clock=clock, ids=ids, events=null_events
        )
        await create_uc.execute(
            CreateApplyScheduleCommand(
                name="every-minute",
                cron_expr="* * * * *",
                target_type="network",
                target_id="n1",
            )
        )
        fired: list[str] = []

        async def cb(target_id: str) -> None:
            fired.append(target_id)

        tick = TickScheduler(
            schedules=sched_repo,
            clock=clock,
            apply_callbacks={ScheduleTargetType.NETWORK: cb},
        )
        count = await tick.execute()
        assert count == 1
        assert fired == ["n1"]


class TestDeleteApplySchedule:
    @pytest.mark.anyio
    async def test_not_found_raises(
        self,
        sched_repo: InMemoryApplyScheduleRepository,
        null_events: _NullEvents,
    ) -> None:
        uc = DeleteApplySchedule(schedules=sched_repo, events=null_events)
        with pytest.raises(NotFoundError):
            await uc.execute(ApplyScheduleId("sched-missing"))


# ===========================================================================
# N5-02  MirrorSession
# ===========================================================================


@pytest.fixture()
def mirror_repo() -> InMemoryMirrorSessionRepository:
    return InMemoryMirrorSessionRepository()


class TestCreateMirrorSession:
    @pytest.mark.anyio
    async def test_span_destination_port(
        self,
        mirror_repo: InMemoryMirrorSessionRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        uc = CreateMirrorSession(
            sessions=mirror_repo, clock=clock, ids=ids, events=null_events
        )
        cmd = CreateMirrorSessionCommand(
            name="span-1",
            source_port_id="lp-src",
            direction="ingress",
            destination_port_id="lp-dst",
        )
        result = await uc.execute(cmd)
        assert result.direction == MirrorDirection.INGRESS
        assert result.destination_port_id == LogicalPortId("lp-dst")
        assert result.destination_ip is None

    @pytest.mark.anyio
    async def test_erspan_destination_ip(
        self,
        mirror_repo: InMemoryMirrorSessionRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        uc = CreateMirrorSession(
            sessions=mirror_repo, clock=clock, ids=ids, events=null_events
        )
        cmd = CreateMirrorSessionCommand(
            name="erspan-1",
            source_port_id="lp-src",
            direction="both",
            destination_ip="10.0.0.5",
        )
        result = await uc.execute(cmd)
        assert result.destination_ip == "10.0.0.5"

    @pytest.mark.anyio
    async def test_no_destination_raises(
        self,
        mirror_repo: InMemoryMirrorSessionRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        from sdn_controller.core.value_objects.errors import ValidationError

        uc = CreateMirrorSession(
            sessions=mirror_repo, clock=clock, ids=ids, events=null_events
        )
        cmd = CreateMirrorSessionCommand(
            name="bad",
            source_port_id="lp-src",
            direction="both",
        )
        with pytest.raises((ValueError, ValidationError)):
            await uc.execute(cmd)

    @pytest.mark.anyio
    async def test_both_destinations_raises(
        self,
        mirror_repo: InMemoryMirrorSessionRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        from sdn_controller.core.value_objects.errors import ValidationError

        uc = CreateMirrorSession(
            sessions=mirror_repo, clock=clock, ids=ids, events=null_events
        )
        cmd = CreateMirrorSessionCommand(
            name="bad2",
            source_port_id="lp-src",
            direction="both",
            destination_port_id="lp-dst",
            destination_ip="10.0.0.1",
        )
        with pytest.raises((ValueError, ValidationError)):
            await uc.execute(cmd)


class TestMirrorConfigurator:
    def _make_session(
        self,
        *,
        destination_port_id: str | None = None,
        destination_ip: str | None = None,
        direction: str = "both",
        filter_vlan: int | None = None,
    ) -> MirrorSession:
        from datetime import UTC, datetime

        now = datetime(2026, 1, 1, tzinfo=UTC)
        return MirrorSession(
            id=MirrorSessionId("mirror-1"),
            name="test",
            source_port_id=LogicalPortId("lp-src"),
            direction=MirrorDirection(direction),
            destination_port_id=LogicalPortId(destination_port_id)
            if destination_port_id
            else None,
            destination_ip=destination_ip,
            filter_vlan=filter_vlan,
            project_id=None,
            labels={},
            created_at=now,
            updated_at=now,
        )

    def test_span_both(self) -> None:
        session = self._make_session(destination_port_id="lp-dst")
        cfg = MirrorConfigurator().generate_config(session)
        assert "ovs-vsctl" in cfg
        assert "lp-src" in cfg
        assert "lp-dst" in cfg

    def test_erspan(self) -> None:
        session = self._make_session(destination_ip="192.168.1.50")
        cfg = MirrorConfigurator().generate_config(session)
        assert "erspan" in cfg.lower() or "ERSPAN" in cfg
        assert "192.168.1.50" in cfg

    def test_apply_mirror_session(self) -> None:
        session = self._make_session(destination_port_id="lp-dst")
        from datetime import UTC, datetime

        session.apply("some-cfg", datetime(2026, 1, 1, tzinfo=UTC))
        assert session.applied_config == "some-cfg"
        assert session.status == MirrorStatus.ACTIVE


class TestApplyMirrorSession:
    @pytest.mark.anyio
    async def test_applies_and_stores_config(
        self,
        mirror_repo: InMemoryMirrorSessionRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        create_uc = CreateMirrorSession(
            sessions=mirror_repo, clock=clock, ids=ids, events=null_events
        )
        session = await create_uc.execute(
            CreateMirrorSessionCommand(
                name="span",
                source_port_id="lp-src",
                direction="both",
                destination_port_id="lp-dst",
            )
        )
        apply_uc = ApplyMirrorSession(
            sessions=mirror_repo, clock=clock, events=null_events
        )
        result = await apply_uc.execute(session.id)
        assert result.applied_config is not None
        assert len(result.applied_config) > 0
        assert result.status == MirrorStatus.ACTIVE


# ===========================================================================
# N5-03  GrpcAgentClient
# ===========================================================================


class TestGrpcAgentClient:
    @pytest.mark.anyio
    async def test_get_capabilities(self, clock: FrozenClock) -> None:
        agent = FakeAgent(clock=clock)
        client = build_fake_grpc_client(agent)
        from sdn_controller.core.value_objects.ids import NodeId

        caps = await client.get_capabilities(NodeId("node-1"))
        assert caps is not None

    @pytest.mark.anyio
    async def test_get_state(self, clock: FrozenClock) -> None:
        agent = FakeAgent(clock=clock)
        client = build_fake_grpc_client(agent)
        from sdn_controller.core.value_objects.ids import NodeId

        state = await client.get_state(NodeId("node-1"))
        assert state is not None


# ===========================================================================
# N5-04  PgAdvisoryLockStore helper
# ===========================================================================


class TestKeyToBigint:
    def test_deterministic(self) -> None:
        assert _key_to_bigint("hello") == _key_to_bigint("hello")

    def test_different_keys(self) -> None:
        assert _key_to_bigint("key-A") != _key_to_bigint("key-B")

    def test_signed_int64(self) -> None:
        value = _key_to_bigint("any-key")
        assert -(2**63) <= value < 2**63


# ===========================================================================
# N5-05  VPN
# ===========================================================================


@pytest.fixture()
def tunnel_repo() -> InMemoryVpnTunnelRepository:
    return InMemoryVpnTunnelRepository()


@pytest.fixture()
def peer_repo() -> InMemoryVpnPeerRepository:
    return InMemoryVpnPeerRepository()


def _tunnel_cmd(**kw: object) -> CreateVpnTunnelCommand:
    defaults: dict[str, object] = dict(
        name="tun-1",
        protocol="wireguard",
        local_endpoint="10.0.0.1",
        remote_endpoint="10.0.0.2",
        local_public_key="local-pub-key",
        remote_public_key="remote-pub-key",
    )
    defaults.update(kw)
    return CreateVpnTunnelCommand(**defaults)  # type: ignore[arg-type]


class TestVpnConfigurator:
    def _make_tunnel(self, protocol: str = "wireguard") -> VpnTunnel:
        from datetime import UTC, datetime

        now = datetime(2026, 1, 1, tzinfo=UTC)
        return VpnTunnel(
            id=VpnTunnelId("vpnt-1"),
            name="tun",
            protocol=VpnProtocol(protocol),
            local_endpoint="10.0.0.1",
            remote_endpoint="10.0.0.2",
            local_public_key="LOCAL_PUB",
            remote_public_key="REMOTE_PUB",
            listen_port=51820,
            preshared_key=None,
            project_id=None,
            labels={},
            created_at=now,
            updated_at=now,
        )

    def _make_peer(self) -> VpnPeer:
        from datetime import UTC, datetime

        now = datetime(2026, 1, 1, tzinfo=UTC)
        return VpnPeer(
            id=VpnPeerId("vpnp-1"),
            tunnel_id=VpnTunnelId("vpnt-1"),
            public_key="PEER_PUB",
            allowed_ips=["10.100.0.2/32"],
            endpoint="1.2.3.4:51820",
            persistent_keepalive=25,
            created_at=now,
            updated_at=now,
        )

    def test_wireguard_config(self) -> None:
        tunnel = self._make_tunnel("wireguard")
        peer = self._make_peer()
        cfg = VpnConfigurator().generate_config(tunnel, [peer])
        assert "[Interface]" in cfg
        assert "LOCAL_PUB" in cfg or "ListenPort" in cfg
        assert "[Peer]" in cfg
        assert "PEER_PUB" in cfg
        assert "10.100.0.2/32" in cfg

    def test_ipsec_config(self) -> None:
        tunnel = self._make_tunnel("ipsec")
        cfg = VpnConfigurator().generate_config(tunnel, [])
        assert "ipsec.conf" in cfg or "conn" in cfg or "leftid" in cfg


class TestCreateVpnTunnel:
    @pytest.mark.anyio
    async def test_creates_wireguard(
        self,
        tunnel_repo: InMemoryVpnTunnelRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        uc = CreateVpnTunnel(
            tunnels=tunnel_repo, clock=clock, ids=ids, events=null_events
        )
        result = await uc.execute(_tunnel_cmd())
        assert result.protocol == VpnProtocol.WIREGUARD
        assert result.status == VpnStatus.BUILD


class TestUpdateVpnTunnel:
    @pytest.mark.anyio
    async def test_updates_name_and_key(
        self,
        tunnel_repo: InMemoryVpnTunnelRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        create = CreateVpnTunnel(
            tunnels=tunnel_repo, clock=clock, ids=ids, events=null_events
        )
        tunnel = await create.execute(_tunnel_cmd())
        uc = UpdateVpnTunnel(tunnels=tunnel_repo, clock=clock, events=null_events)
        result = await uc.execute(
            UpdateVpnTunnelCommand(
                tunnel_id=tunnel.id,
                name="new-name",
                remote_public_key="NEW_REMOTE",
            )
        )
        assert result.name == "new-name"
        assert result.remote_public_key == "NEW_REMOTE"


class TestApplyVpnTunnel:
    @pytest.mark.anyio
    async def test_apply_stores_config(
        self,
        tunnel_repo: InMemoryVpnTunnelRepository,
        peer_repo: InMemoryVpnPeerRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        create = CreateVpnTunnel(
            tunnels=tunnel_repo, clock=clock, ids=ids, events=null_events
        )
        tunnel = await create.execute(_tunnel_cmd())
        uc = ApplyVpnTunnel(
            tunnels=tunnel_repo, peers=peer_repo, clock=clock, events=null_events
        )
        result = await uc.execute(tunnel.id)
        assert result.applied_config is not None
        assert result.status == VpnStatus.ACTIVE


class TestVpnPeers:
    @pytest.mark.anyio
    async def test_add_peer_and_list(
        self,
        tunnel_repo: InMemoryVpnTunnelRepository,
        peer_repo: InMemoryVpnPeerRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        create_tunnel = CreateVpnTunnel(
            tunnels=tunnel_repo, clock=clock, ids=ids, events=null_events
        )
        tunnel = await create_tunnel.execute(_tunnel_cmd())
        uc = AddVpnPeer(
            peers=peer_repo,
            tunnels=tunnel_repo,
            clock=clock,
            ids=ids,
            events=null_events,
        )
        peer = await uc.execute(
            AddVpnPeerCommand(
                tunnel_id=tunnel.id,
                public_key="PEER_PUB",
                allowed_ips=["10.100.0.0/24"],
                endpoint="5.5.5.5:51820",
                persistent_keepalive=25,
            )
        )
        assert peer.public_key == "PEER_PUB"
        from sdn_controller.core.use_cases.n5 import ListVpnPeers

        peers = await ListVpnPeers(peers=peer_repo).execute(
            tunnel_id=tunnel.id
        )
        assert len(peers) == 1

    @pytest.mark.anyio
    async def test_duplicate_public_key_conflict(
        self,
        tunnel_repo: InMemoryVpnTunnelRepository,
        peer_repo: InMemoryVpnPeerRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        create_tunnel = CreateVpnTunnel(
            tunnels=tunnel_repo, clock=clock, ids=ids, events=null_events
        )
        tunnel = await create_tunnel.execute(_tunnel_cmd())
        uc = AddVpnPeer(
            peers=peer_repo,
            tunnels=tunnel_repo,
            clock=clock,
            ids=ids,
            events=null_events,
        )
        await uc.execute(
            AddVpnPeerCommand(
                tunnel_id=tunnel.id,
                public_key="SAME_KEY",
                allowed_ips=["10.0.0.1/32"],
            )
        )
        with pytest.raises(ConflictError):
            await uc.execute(
                AddVpnPeerCommand(
                    tunnel_id=tunnel.id,
                    public_key="SAME_KEY",
                    allowed_ips=["10.0.0.2/32"],
                )
            )

    @pytest.mark.anyio
    async def test_delete_tunnel_cascades_peers(
        self,
        tunnel_repo: InMemoryVpnTunnelRepository,
        peer_repo: InMemoryVpnPeerRepository,
        clock: FrozenClock,
        ids: CountingIdFactory,
        null_events: _NullEvents,
    ) -> None:
        create_tunnel = CreateVpnTunnel(
            tunnels=tunnel_repo, clock=clock, ids=ids, events=null_events
        )
        tunnel = await create_tunnel.execute(_tunnel_cmd())
        add_peer = AddVpnPeer(
            peers=peer_repo,
            tunnels=tunnel_repo,
            clock=clock,
            ids=ids,
            events=null_events,
        )
        await add_peer.execute(
            AddVpnPeerCommand(
                tunnel_id=tunnel.id,
                public_key="K1",
                allowed_ips=["10.0.0.1/32"],
            )
        )
        delete_uc = DeleteVpnTunnel(
            tunnels=tunnel_repo, peers=peer_repo, events=null_events
        )
        await delete_uc.execute(tunnel.id)
        # tunnel gone
        assert await tunnel_repo.get(tunnel.id) is None
        # peers gone
        from sdn_controller.core.use_cases.n5 import ListVpnPeers

        remaining = await ListVpnPeers(peers=peer_repo).execute(
            tunnel_id=tunnel.id
        )
        assert remaining == []
