"""Local-host system info / stats provider (uses stdlib + best-effort sysfs)."""

from netos_agent.adapters.system_local.adapter import LocalSystemInfo

__all__ = ["LocalSystemInfo"]
