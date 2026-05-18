"""DHCP port — what the agent expects from any DHCP backend.

Two production implementations (dnsmasq today, Kea later) and a fake will
all sit behind this Protocol. Methods are deliberately granular so the
plan dispatcher can call exactly the right one for each step.

``validate`` exists separately from ``apply`` because the plan acceptance
criterion is "config проверяется перед применением" — the dispatcher runs
the validation first and only then commits the config file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from netos_agent.core.value_objects.edge_services import DhcpScopeSpec


@dataclass(frozen=True, slots=True)
class DhcpLease:
    ip_address: str
    mac_address: str
    hostname: str | None
    expires_at_epoch: int


class DhcpPort(Protocol):
    async def validate(self, scope: DhcpScopeSpec) -> None:
        """Pre-flight a scope's config. Raises ``OvsdbError``-equivalent on bad config.

        The Protocol stays backend-agnostic; concrete adapters translate
        their tool's failure into ``OvsdbError`` so the dispatcher can
        report a single shape.
        """

    async def apply(self, scope: DhcpScopeSpec) -> bool:
        """Install/refresh a scope. ``True`` if anything actually changed."""

    async def delete(self, scope_id: str) -> bool:
        """Remove a scope by id. ``True`` if a scope was removed."""

    async def list_scopes(self) -> list[DhcpScopeSpec]:
        """Return the scopes currently installed."""

    async def get_leases(self, scope_id: str) -> list[DhcpLease]:
        """Active leases for one scope."""
