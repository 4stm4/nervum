"""Dependency container for the agent.

Mirrors the controller's container shape: constructor injection, one
container instance per process, no implicit globals. ``build_container``
branches on ``settings.ovs_backend`` to pick the OVSDB adapter — keeping
``fake`` and ``subprocess`` substitutable on the same protocol.
"""

from __future__ import annotations

from dataclasses import dataclass

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
from netos_agent.ports.ovsdb import OvsdbPort
from netos_agent.ports.snapshots import SnapshotRepository
from netos_agent.ports.system import SystemInfoPort


@dataclass(slots=True)
class Container:
    settings: Settings
    clock: Clock
    ids: IdFactory

    ovsdb: OvsdbPort
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
    snapshots_repo: SnapshotRepository = FsSnapshotRepository(settings.snapshots_dir)
    system: SystemInfoPort = LocalSystemInfo()

    return Container(
        settings=settings,
        clock=clock,
        ids=ids,
        ovsdb=ovsdb,
        snapshots_repo=snapshots_repo,
        system=system,
        apply_plan=ApplyPlan(ovsdb=ovsdb),
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
