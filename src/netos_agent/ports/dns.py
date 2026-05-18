"""DNS port — adapter contract for serving authoritative zones."""

from __future__ import annotations

from typing import Protocol

from netos_agent.core.value_objects.edge_services import DnsZoneSpec


class DnsPort(Protocol):
    async def validate(self, zone: DnsZoneSpec) -> None: ...
    async def apply(self, zone: DnsZoneSpec) -> bool: ...
    async def delete(self, zone: str) -> bool: ...
    async def list_zones(self) -> list[DnsZoneSpec]: ...
    async def resolve_check(self, zone: str, name: str) -> str | None:
        """Resolve ``name`` inside ``zone``. Used by ``/readyz``-style checks."""
