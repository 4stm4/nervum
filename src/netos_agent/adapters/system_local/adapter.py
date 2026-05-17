"""``LocalSystemInfo`` — best-effort facts about the host.

Implementation choice: stdlib-only. Avoids pulling ``psutil`` for two
methods worth of data. Anything not reliably knowable on a portable
filesystem (e.g. memory_used on macOS without ``vm_stat``) is returned as
``None`` — better than a wrong number.
"""

from __future__ import annotations

import os
import platform
import time
from pathlib import Path

from netos_agent.core.value_objects.system_info import SystemInfo, SystemStats


class LocalSystemInfo:
    def __init__(self) -> None:
        # ``info`` is process-lifetime stable: cache it after first read.
        self._cached_info: SystemInfo | None = None
        self._boot_time = time.monotonic()

    async def info(self) -> SystemInfo:
        if self._cached_info is None:
            self._cached_info = SystemInfo(
                hostname=platform.node() or "unknown",
                kernel=_kernel_release(),
                cpu_count=os.cpu_count(),
                memory_total_bytes=_memory_total(),
            )
        return self._cached_info

    async def stats(self) -> SystemStats:
        load_1m, load_5m, load_15m = _loadavg()
        return SystemStats(
            uptime_seconds=time.monotonic() - self._boot_time,
            load_avg_1m=load_1m,
            load_avg_5m=load_5m,
            load_avg_15m=load_15m,
            memory_used_bytes=None,  # left for a future libc-specific implementation
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kernel_release() -> str | None:
    rel = platform.release()
    return rel or None


_MEMINFO_KB_TO_BYTES = 1024
_MEMINFO_MIN_FIELDS = 2  # ``MemTotal:`` ``<n>`` ``kB``


def _memory_total() -> int | None:
    """Linux: /proc/meminfo. Other platforms: not portable, return None."""
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    try:
        text = meminfo.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= _MEMINFO_MIN_FIELDS:
                try:
                    return int(parts[1]) * _MEMINFO_KB_TO_BYTES
                except ValueError:
                    return None
    return None


def _loadavg() -> tuple[float | None, float | None, float | None]:
    try:
        l1, l5, l15 = os.getloadavg()
    except OSError:
        return (None, None, None)
    return (l1, l5, l15)
