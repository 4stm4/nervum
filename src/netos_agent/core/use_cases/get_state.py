"""Read use cases for OVS and node state."""

from __future__ import annotations

from dataclasses import dataclass

from netos_agent.core.entities import OvsState
from netos_agent.core.value_objects.system_info import SystemInfo, SystemStats
from netos_agent.ports.ovsdb import OvsdbPort
from netos_agent.ports.system import SystemInfoPort


@dataclass(frozen=True, slots=True)
class NodeState:
    """Composite view returned by ``GET /v1/node/state``."""

    info: SystemInfo
    ovs_state: OvsState


class GetOvsState:
    def __init__(self, *, ovsdb: OvsdbPort) -> None:
        self._ovsdb = ovsdb

    async def execute(self) -> OvsState:
        return await self._ovsdb.get_state()


class GetSystemInfo:
    def __init__(self, *, system: SystemInfoPort) -> None:
        self._system = system

    async def execute(self) -> SystemInfo:
        return await self._system.info()


class GetSystemStats:
    def __init__(self, *, system: SystemInfoPort) -> None:
        self._system = system

    async def execute(self) -> SystemStats:
        return await self._system.stats()


class GetNodeState:
    """``info`` + live OVS state in one round-trip, matching the plan's
    ``/v1/node/state`` endpoint."""

    def __init__(self, *, ovsdb: OvsdbPort, system: SystemInfoPort) -> None:
        self._ovsdb = ovsdb
        self._system = system

    async def execute(self) -> NodeState:
        info = await self._system.info()
        state = await self._ovsdb.get_state()
        return NodeState(info=info, ovs_state=state)
