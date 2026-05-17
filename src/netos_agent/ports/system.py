"""System info / stats port.

Two methods — ``info`` is slow-changing, ``stats`` is the snapshot of
live counters. Split because they have very different cache lifetimes:
info is cached for the process lifetime; stats are computed per-request.
"""

from __future__ import annotations

from typing import Protocol

from netos_agent.core.value_objects.system_info import SystemInfo, SystemStats


class SystemInfoPort(Protocol):
    async def info(self) -> SystemInfo: ...
    async def stats(self) -> SystemStats: ...
