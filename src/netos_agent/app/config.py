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

    # Edge-service backends. Each defaults to ``fake`` so the agent can
    # boot on any machine; flip to the production backend per service.
    dhcp_backend: Literal["fake", "dnsmasq"] = "fake"
    dnsmasq_path: str = "dnsmasq"
    dnsmasq_config_dir: str = "/etc/dnsmasq.d"
    dnsmasq_lease_file: str = "/var/lib/misc/dnsmasq.leases"
    dnsmasq_pid_file: str = "/run/dnsmasq/dnsmasq.pid"

    dns_backend: Literal["fake", "coredns"] = "fake"
    coredns_path: str = "coredns"
    coredns_zones_dir: str = "/etc/coredns/zones"
    coredns_corefile: str = "/etc/coredns/sdn-Corefile"
    coredns_pid_file: str = "/run/coredns/coredns.pid"

    firewall_backend: Literal["fake", "nftables"] = "fake"
    nft_path: str = "nft"
    nft_scratch_dir: str = "/run/sdn-controller/nft"


def load_settings() -> Settings:
    return Settings()
