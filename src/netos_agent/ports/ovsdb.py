"""OVSDB port.

The use cases see this contract; the concrete adapters (``FakeOvsdb`` for
tests / dev, ``SubprocessOvsdb`` for a real node) implement it. Each ``ensure_*``
method returns ``True`` when it actually changed state and ``False`` if the
target already matched — that's the idempotency signal surfaced in
``PlanStepResult.changed``.

``dump`` / ``restore`` exist to serve snapshot/rollback. Their payload is
opaque to the core: any JSON-serialisable shape works as long as the same
adapter can round-trip it.
"""

from __future__ import annotations

from typing import Any, Protocol

from netos_agent.core.entities import OvsState


class OvsdbPort(Protocol):
    async def get_state(self) -> OvsState: ...

    async def ensure_bridge(
        self,
        *,
        name: str,
        datapath_type: str = "system",
        external_ids: dict[str, str] | None = None,
    ) -> bool: ...
    async def delete_bridge(self, *, name: str) -> bool: ...

    async def ensure_port(
        self,
        *,
        bridge: str,
        name: str,
        type: str = "internal",
        options: dict[str, str] | None = None,
        tag: int | None = None,
        trunks: tuple[int, ...] = (),
        external_ids: dict[str, str] | None = None,
    ) -> bool: ...
    async def delete_port(self, *, bridge: str, name: str) -> bool: ...

    async def ensure_vxlan_port(
        self,
        *,
        bridge: str,
        name: str,
        vni: int,
        remote_ip: str,
        local_ip: str | None = None,
        dst_port: int = 4789,
        mtu: int | None = None,
        external_ids: dict[str, str] | None = None,
    ) -> bool: ...

    async def dump(self) -> dict[str, Any]: ...
    async def restore(self, payload: dict[str, Any]) -> None: ...
