"""Agent runtime configuration.

All values come from environment variables with the ``NETOS_AGENT_`` prefix,
e.g. ``NETOS_AGENT_OVS_BACKEND=subprocess``. Defaults boot the agent in
``fake`` mode so we get a usable HTTP service even on machines without OVS.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NETOS_AGENT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["dev", "staging", "prod"] = "dev"
    http_host: str = "0.0.0.0"  # noqa: S104 — operator decides bind via env
    http_port: int = Field(default=9100, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    # OVSDB backend.
    #   "fake"       — in-process, no external dependencies. Default; safe to
    #                  boot the agent on a machine without OVS installed.
    #   "subprocess" — shells out to ``ovs-vsctl``. Requires OVS + permissions.
    ovs_backend: Literal["fake", "subprocess"] = "fake"
    ovs_vsctl_path: str = "ovs-vsctl"
    ovs_vsctl_timeout_seconds: float = Field(default=10.0, ge=0.1, le=120.0)

    # Snapshot store.
    snapshots_dir: str = "./netos-agent-snapshots"


def load_settings() -> Settings:
    return Settings()
