"""In-memory ``DhcpPort`` — keeps scopes in a dict, never touches dnsmasq.

Idempotency: ``apply`` returns ``True`` only when the stored spec actually
differs from what's already there; otherwise ``False`` so the reconciler
sees ``changed=False``.
"""

from __future__ import annotations

import anyio

from netos_agent.core.value_objects.edge_services import DhcpScopeSpec
from netos_agent.ports.dhcp import DhcpLease


class FakeDhcp:
    def __init__(self) -> None:
        self._scopes: dict[str, DhcpScopeSpec] = {}
        self._leases: dict[str, list[DhcpLease]] = {}
        self._lock = anyio.Lock()

    async def validate(self, scope: DhcpScopeSpec) -> None:
        # ``DhcpScopeSpec.__post_init__`` already runs invariants; nothing
        # backend-specific to add.
        return None

    async def apply(self, scope: DhcpScopeSpec) -> bool:
        async with self._lock:
            existing = self._scopes.get(scope.scope_id)
            if existing == scope:
                return False
            self._scopes[scope.scope_id] = scope
            return True

    async def delete(self, scope_id: str) -> bool:
        async with self._lock:
            return self._scopes.pop(scope_id, None) is not None

    async def list_scopes(self) -> list[DhcpScopeSpec]:
        async with self._lock:
            return list(self._scopes.values())

    async def get_leases(self, scope_id: str) -> list[DhcpLease]:
        async with self._lock:
            return list(self._leases.get(scope_id, ()))

    # -- test helpers (not part of the protocol) --------------------------

    async def seed_lease(self, scope_id: str, lease: DhcpLease) -> None:
        async with self._lock:
            self._leases.setdefault(scope_id, []).append(lease)
