"""``DnsPort`` implementation that just stores zones in a dict.

``resolve_check`` walks the zone records and returns the first A/AAAA hit
for the queried name — enough to give end-to-end tests something to assert
without standing up CoreDNS.
"""

from __future__ import annotations

import anyio

from netos_agent.core.value_objects.edge_services import DnsZoneSpec


class FakeDns:
    def __init__(self) -> None:
        self._zones: dict[str, DnsZoneSpec] = {}
        self._lock = anyio.Lock()

    async def validate(self, zone: DnsZoneSpec) -> None:
        return None

    async def apply(self, zone: DnsZoneSpec) -> bool:
        async with self._lock:
            existing = self._zones.get(zone.zone)
            if existing == zone:
                return False
            self._zones[zone.zone] = zone
            return True

    async def delete(self, zone: str) -> bool:
        async with self._lock:
            return self._zones.pop(zone, None) is not None

    async def list_zones(self) -> list[DnsZoneSpec]:
        async with self._lock:
            return list(self._zones.values())

    async def resolve_check(self, zone: str, name: str) -> str | None:
        async with self._lock:
            spec = self._zones.get(zone)
            if spec is None:
                return None
            for rec in spec.records:
                if rec.name == name and rec.type in {"A", "AAAA"}:
                    return rec.value
            return None
