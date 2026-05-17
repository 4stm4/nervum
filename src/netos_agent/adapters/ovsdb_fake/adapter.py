"""Pure-Python in-memory implementation of ``OvsdbPort``.

The fake stores the same shape that ``OvsState`` exposes, so dumps and
restores round-trip without translation. Every ``ensure_*`` method is
idempotent and returns ``True`` only when it actually changed state — that's
the signal the use cases forward to ``PlanStepResult.changed``.
"""

from __future__ import annotations

from typing import Any

import anyio

from netos_agent.core.entities import (
    BridgeState,
    InterfaceState,
    OvsState,
    PortState,
)
from netos_agent.core.value_objects.errors import NotFoundError, ValidationError


class FakeOvsdb:
    def __init__(self, *, ovs_version: str = "fake-3.2.0") -> None:
        self._ovs_version = ovs_version
        # mutable internal model — only this class touches it
        self._bridges: dict[str, _BridgeMut] = {}
        self._lock = anyio.Lock()

    # -- read ---------------------------------------------------------------

    async def get_state(self) -> OvsState:
        async with self._lock:
            return _to_state(self._ovs_version, self._bridges)

    # -- bridge -------------------------------------------------------------

    async def ensure_bridge(self, *, name: str, datapath_type: str = "system") -> bool:
        async with self._lock:
            existing = self._bridges.get(name)
            if existing is None:
                self._bridges[name] = _BridgeMut(name=name, datapath_type=datapath_type)
                return True
            if existing.datapath_type != datapath_type:
                existing.datapath_type = datapath_type
                return True
            return False

    async def delete_bridge(self, *, name: str) -> bool:
        async with self._lock:
            return self._bridges.pop(name, None) is not None

    # -- port ---------------------------------------------------------------

    async def ensure_port(
        self,
        *,
        bridge: str,
        name: str,
        type: str = "internal",
        options: dict[str, str] | None = None,
        tag: int | None = None,
        trunks: tuple[int, ...] = (),
    ) -> bool:
        opts = dict(options or {})
        async with self._lock:
            br = self._require_bridge(bridge)
            existing = br.ports.get(name)
            new = _PortMut(
                name=name,
                tag=tag,
                trunks=tuple(sorted(trunks)),
                interfaces={
                    name: _InterfaceMut(name=name, type=type, options=dict(sorted(opts.items())))
                },
            )
            if existing is None:
                br.ports[name] = new
                return True
            if _ports_equal(existing, new):
                return False
            br.ports[name] = new
            return True

    async def delete_port(self, *, bridge: str, name: str) -> bool:
        async with self._lock:
            br = self._require_bridge(bridge)
            return br.ports.pop(name, None) is not None

    async def ensure_vxlan_port(
        self,
        *,
        bridge: str,
        name: str,
        vni: int,
        remote_ip: str,
        dst_port: int = 4789,
    ) -> bool:
        # Modelled as a port whose single interface is type=vxlan with the
        # tunnel options OVS expects.
        return await self.ensure_port(
            bridge=bridge,
            name=name,
            type="vxlan",
            options={
                "key": str(vni),
                "remote_ip": remote_ip,
                "dst_port": str(dst_port),
            },
        )

    # -- snapshot / restore -------------------------------------------------

    async def dump(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "ovs_version": self._ovs_version,
                "bridges": [b.to_dict() for b in self._bridges.values()],
            }

    async def restore(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._ovs_version = str(payload.get("ovs_version") or self._ovs_version)
            self._bridges = {b["name"]: _BridgeMut.from_dict(b) for b in payload.get("bridges", [])}

    # -- helpers ------------------------------------------------------------

    def _require_bridge(self, name: str) -> _BridgeMut:
        try:
            return self._bridges[name]
        except KeyError as exc:
            raise NotFoundError(f"bridge {name!r} does not exist") from exc


# ---------------------------------------------------------------------------
# Mutable internal model — kept private to the adapter
# ---------------------------------------------------------------------------


class _InterfaceMut:
    __slots__ = ("name", "options", "type")

    def __init__(self, *, name: str, type: str, options: dict[str, str]) -> None:
        self.name = name
        self.type = type
        self.options = options

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "options": dict(self.options),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> _InterfaceMut:
        if "name" not in d:
            raise ValidationError("interface dump missing 'name'")
        return cls(
            name=str(d["name"]),
            type=str(d.get("type", "internal")),
            options=dict(d.get("options") or {}),
        )

    def to_state(self) -> InterfaceState:
        return InterfaceState(name=self.name, type=self.type, options=dict(self.options))


class _PortMut:
    __slots__ = ("interfaces", "name", "tag", "trunks")

    def __init__(
        self,
        *,
        name: str,
        tag: int | None,
        trunks: tuple[int, ...],
        interfaces: dict[str, _InterfaceMut],
    ) -> None:
        self.name = name
        self.tag = tag
        self.trunks = trunks
        self.interfaces = interfaces

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tag": self.tag,
            "trunks": list(self.trunks),
            "interfaces": [i.to_dict() for i in self.interfaces.values()],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> _PortMut:
        if "name" not in d:
            raise ValidationError("port dump missing 'name'")
        interfaces = {i["name"]: _InterfaceMut.from_dict(i) for i in d.get("interfaces") or []}
        return cls(
            name=str(d["name"]),
            tag=d.get("tag"),
            trunks=tuple(int(x) for x in (d.get("trunks") or ())),
            interfaces=interfaces,
        )

    def to_state(self) -> PortState:
        return PortState(
            name=self.name,
            tag=self.tag,
            trunks=tuple(sorted(self.trunks)),
            interfaces=tuple(i.to_state() for i in self.interfaces.values()),
        )


class _BridgeMut:
    __slots__ = ("datapath_type", "name", "ports")

    def __init__(
        self,
        *,
        name: str,
        datapath_type: str = "system",
        ports: dict[str, _PortMut] | None = None,
    ) -> None:
        self.name = name
        self.datapath_type = datapath_type
        self.ports: dict[str, _PortMut] = ports or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "datapath_type": self.datapath_type,
            "ports": [p.to_dict() for p in self.ports.values()],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> _BridgeMut:
        if "name" not in d:
            raise ValidationError("bridge dump missing 'name'")
        return cls(
            name=str(d["name"]),
            datapath_type=str(d.get("datapath_type", "system")),
            ports={p["name"]: _PortMut.from_dict(p) for p in d.get("ports") or []},
        )

    def to_state(self) -> BridgeState:
        return BridgeState(
            name=self.name,
            datapath_type=self.datapath_type,
            ports=tuple(p.to_state() for p in self.ports.values()),
        )


def _to_state(version: str | None, bridges: dict[str, _BridgeMut]) -> OvsState:
    return OvsState(
        ovs_version=version,
        bridges=tuple(b.to_state() for b in bridges.values()),
    )


def _ports_equal(a: _PortMut, b: _PortMut) -> bool:
    if a.tag != b.tag or a.trunks != b.trunks:
        return False
    if set(a.interfaces.keys()) != set(b.interfaces.keys()):
        return False
    for k, ai in a.interfaces.items():
        bi = b.interfaces[k]
        if ai.type != bi.type or ai.options != bi.options:
            return False
    return True
