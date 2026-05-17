"""System-level facts the agent reports up to the controller.

Two flavours:

* ``SystemInfo`` — slow-changing identity (hostname, kernel, cpu count,
  memory) used in node info / capabilities responses.
* ``SystemStats`` — point-in-time observations (uptime_seconds, load average,
  optionally memory used) returned by the ``/system/stats`` endpoint.

Both are pure value objects; the adapter that fills them lives outside the
core.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SystemInfo:
    hostname: str
    kernel: str | None = None
    cpu_count: int | None = None
    memory_total_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class SystemStats:
    uptime_seconds: float | None = None
    load_avg_1m: float | None = None
    load_avg_5m: float | None = None
    load_avg_15m: float | None = None
    memory_used_bytes: int | None = None
