"""Dependency container for the agent.

Mirrors the controller's container shape: constructor injection, one
container instance per process, no implicit globals. ``build_container``
branches on the backend selectors so each edge service can be ``fake`` or
production-real independently — useful when one operator runs the agent
on a machine that has nftables but no dnsmasq, for example.
"""

from __future__ import annotations

from dataclasses import dataclass

from netos_agent.adapters.dhcp_dnsmasq import DnsmasqDhcp
from netos_agent.adapters.dhcp_fake import FakeDhcp
from netos_agent.adapters.dns_coredns import CorednsDns
from netos_agent.adapters.dns_fake import FakeDns
from netos_agent.adapters.firewall_fake import FakeFirewall
from netos_agent.adapters.firewall_nftables import NftablesFirewall
from netos_agent.adapters.ovsdb_fake import FakeOvsdb
from netos_agent.adapters.ovsdb_subprocess import SubprocessOvsdb
from netos_agent.adapters.snapshots_fs import FsSnapshotRepository
from netos_agent.adapters.system_local import LocalSystemInfo
from netos_agent.app.config import Settings
from netos_agent.core.services.clock import Clock, SystemClock
from netos_agent.core.use_cases.apply_plan import ApplyPlan
from netos_agent.core.use_cases.get_state import (
    GetNodeState,
    GetOvsState,
    GetSystemInfo,
    GetSystemStats,
)
from netos_agent.core.use_cases.snapshots import ListSnapshots, Restore, Snapshot
from netos_agent.core.value_objects.ids import IdFactory, UuidIdFactory
from netos_agent.ports.dhcp import DhcpPort
from netos_agent.ports.dns import DnsPort
from netos_agent.ports.firewall import FirewallPort
from netos_agent.ports.ovsdb import OvsdbPort
from netos_agent.ports.snapshots import SnapshotRepository
from netos_agent.ports.system import SystemInfoPort


@dataclass(slots=True)
class Container:
    settings: Settings
    clock: Clock
    ids: IdFactory

    ovsdb: OvsdbPort
    dhcp: DhcpPort
    dns: DnsPort
    firewall: FirewallPort
    snapshots_repo: SnapshotRepository
    system: SystemInfoPort

    apply_plan: ApplyPlan
    get_ovs_state: GetOvsState
    get_node_state: GetNodeState
    get_system_info: GetSystemInfo
    get_system_stats: GetSystemStats
    snapshot: Snapshot
    restore: Restore
    list_snapshots: ListSnapshots

    async def shutdown(self) -> None:
        # Nothing here today; reserved for futures (e.g. closing a JSON-RPC
        # connection to OVSDB or flushing a snapshot writer).
        return None


def build_container(settings: Settings) -> Container:
    clock: Clock = SystemClock()
    ids: IdFactory = UuidIdFactory()

    ovsdb: OvsdbPort = _build_ovsdb(settings)
    dhcp: DhcpPort = _build_dhcp(settings)
    dns: DnsPort = _build_dns(settings)
    firewall: FirewallPort = _build_firewall(settings)
    snapshots_repo: SnapshotRepository = FsSnapshotRepository(settings.snapshots_dir)
    system: SystemInfoPort = LocalSystemInfo()

    return Container(
        settings=settings,
        clock=clock,
        ids=ids,
        ovsdb=ovsdb,
        dhcp=dhcp,
        dns=dns,
        firewall=firewall,
        snapshots_repo=snapshots_repo,
        system=system,
        apply_plan=ApplyPlan(ovsdb=ovsdb, dhcp=dhcp, dns=dns, firewall=firewall),
        get_ovs_state=GetOvsState(ovsdb=ovsdb),
        get_node_state=GetNodeState(ovsdb=ovsdb, system=system),
        get_system_info=GetSystemInfo(system=system),
        get_system_stats=GetSystemStats(system=system),
        snapshot=Snapshot(ovsdb=ovsdb, snapshots=snapshots_repo, clock=clock, ids=ids),
        restore=Restore(ovsdb=ovsdb, snapshots=snapshots_repo),
        list_snapshots=ListSnapshots(snapshots=snapshots_repo),
    )


def _build_ovsdb(settings: Settings) -> OvsdbPort:
    if settings.ovs_backend == "fake":
        return FakeOvsdb()
    if settings.ovs_backend == "subprocess":
        return SubprocessOvsdb(
            ovs_vsctl=settings.ovs_vsctl_path,
            timeout=settings.ovs_vsctl_timeout_seconds,
        )
    raise NotImplementedError(f"unsupported ovs_backend: {settings.ovs_backend!r}")


def _build_dhcp(settings: Settings) -> DhcpPort:
    if settings.dhcp_backend == "fake":
        return FakeDhcp()
    if settings.dhcp_backend == "dnsmasq":
        return DnsmasqDhcp(
            dnsmasq=settings.dnsmasq_path,
            config_dir=settings.dnsmasq_config_dir,
            lease_file=settings.dnsmasq_lease_file,
            pid_file=settings.dnsmasq_pid_file,
        )
    raise NotImplementedError(f"unsupported dhcp_backend: {settings.dhcp_backend!r}")


def _build_dns(settings: Settings) -> DnsPort:
    if settings.dns_backend == "fake":
        return FakeDns()
    if settings.dns_backend == "coredns":
        return CorednsDns(
            coredns=settings.coredns_path,
            zones_dir=settings.coredns_zones_dir,
            corefile_path=settings.coredns_corefile,
            pid_file=settings.coredns_pid_file,
        )
    raise NotImplementedError(f"unsupported dns_backend: {settings.dns_backend!r}")


def _build_firewall(settings: Settings) -> FirewallPort:
    if settings.firewall_backend == "fake":
        return FakeFirewall()
    if settings.firewall_backend == "nftables":
        return NftablesFirewall(
            nft=settings.nft_path,
            scratch_dir=settings.nft_scratch_dir,
        )
    raise NotImplementedError(f"unsupported firewall_backend: {settings.firewall_backend!r}")
