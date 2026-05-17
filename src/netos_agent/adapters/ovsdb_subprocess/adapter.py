"""Real OVS adapter using ``ovs-vsctl`` and ``ovsdb-client``.

This adapter is **not unit-tested** — exercising it requires a host with a
running ``ovs-vswitchd`` and ``ovsdb-server``. We keep its surface very thin
so a future replacement that talks OVSDB JSON-RPC directly can drop in
without touching the use cases.

For Milestone 3 the read path is implemented (``ovs-vsctl --format=json
list-br/list-ports/list interface``) so an integration check on a real node
returns sensible state. Mutating methods are placeholders that delegate to
``ovs-vsctl`` commands — they will be fleshed out in Milestone 4 when we
ship the full OVS adapter feature set (VLAN/VXLAN, snapshots).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from netos_agent.core.entities import (
    BridgeState,
    InterfaceState,
    OvsState,
    PortState,
)
from netos_agent.core.value_objects.errors import OvsdbError

_DEFAULT_TIMEOUT_S = 10.0


class SubprocessOvsdb:
    def __init__(
        self,
        *,
        ovs_vsctl: str = "ovs-vsctl",
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._ovs_vsctl = ovs_vsctl
        self._timeout = timeout

    # -- read ---------------------------------------------------------------

    async def get_state(self) -> OvsState:
        if shutil.which(self._ovs_vsctl) is None:
            raise OvsdbError(f"{self._ovs_vsctl!r} not found on PATH")

        version_raw = await self._run(self._ovs_vsctl, "--version")
        ovs_version = _parse_version(version_raw)

        bridges_raw = await self._run(
            self._ovs_vsctl,
            "--format=json",
            "--columns=name,datapath_type,ports",
            "list",
            "Bridge",
        )
        ports_raw = await self._run(
            self._ovs_vsctl,
            "--format=json",
            "--columns=_uuid,name,tag,trunks,interfaces",
            "list",
            "Port",
        )
        ifaces_raw = await self._run(
            self._ovs_vsctl,
            "--format=json",
            "--columns=_uuid,name,type,options",
            "list",
            "Interface",
        )
        bridges = _parse_bridges(bridges_raw, ports_raw, ifaces_raw)
        return OvsState(ovs_version=ovs_version, bridges=tuple(bridges))

    # -- write --------------------------------------------------------------
    #
    # M3 wires the minimum needed for the controller to issue smoke plans.
    # The "did this change anything" answer is derived by comparing state
    # before and after; ``ovs-vsctl`` itself does not report this directly.

    async def ensure_bridge(
        self,
        *,
        name: str,
        datapath_type: str = "system",
        external_ids: dict[str, str] | None = None,
    ) -> bool:
        before = await self._has_bridge(name)
        args = [
            self._ovs_vsctl,
            "--may-exist",
            "add-br",
            name,
            "--",
            "set",
            "Bridge",
            name,
            f"datapath_type={datapath_type}",
        ]
        for k, v in (external_ids or {}).items():
            args.extend(["--", "set", "Bridge", name, f"external_ids:{k}={v}"])
        await self._run(*args)
        return not before  # M4 still claims "changed=True on creation"; richer diff later

    async def delete_bridge(self, *, name: str) -> bool:
        before = await self._has_bridge(name)
        await self._run(self._ovs_vsctl, "--if-exists", "del-br", name)
        return before

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
    ) -> bool:
        # Bridge-and-port create. ``ovs-vsctl --may-exist add-port`` is
        # idempotent for the port itself; setting columns is run unconditionally
        # because ovs-vsctl does not have a "set if different" mode.
        args = [
            self._ovs_vsctl,
            "--may-exist",
            "add-port",
            bridge,
            name,
            "--",
            "set",
            "Interface",
            name,
            f"type={type}",
        ]
        for k, v in (options or {}).items():
            args.extend(["--", "set", "Interface", name, f"options:{k}={v}"])
        if tag is not None:
            args.extend(["--", "set", "Port", name, f"tag={tag}"])
        if trunks:
            joined = ",".join(str(t) for t in sorted(trunks))
            args.extend(["--", "set", "Port", name, f"trunks=[{joined}]"])
        for k, v in (external_ids or {}).items():
            args.extend(["--", "set", "Port", name, f"external_ids:{k}={v}"])
        await self._run(*args)
        # For now we always claim "changed". M5 will diff before/after.
        return True

    async def delete_port(self, *, bridge: str, name: str) -> bool:
        before = await self._port_exists(bridge, name)
        await self._run(self._ovs_vsctl, "--if-exists", "del-port", bridge, name)
        return before

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
    ) -> bool:
        options: dict[str, str] = {
            "key": str(vni),
            "remote_ip": remote_ip,
            "dst_port": str(dst_port),
        }
        if local_ip is not None:
            options["local_ip"] = local_ip
        if mtu is not None:
            options["mtu_request"] = str(mtu)
        return await self.ensure_port(
            bridge=bridge,
            name=name,
            type="vxlan",
            options=options,
            external_ids=external_ids,
        )

    # -- snapshot / restore -------------------------------------------------
    #
    # ``ovsdb-client backup`` produces a database-level dump. For M3 we
    # raise so the operator picks ``FakeOvsdb`` for snapshot-based flows
    # until M4 wires the real path.

    async def dump(self) -> dict[str, Any]:
        raise OvsdbError("subprocess OVSDB dump is not implemented in M3; use FakeOvsdb for now")

    async def restore(self, payload: dict[str, Any]) -> None:
        raise OvsdbError("subprocess OVSDB restore is not implemented in M3; use FakeOvsdb for now")

    # -- private ------------------------------------------------------------

    async def _has_bridge(self, name: str) -> bool:
        try:
            out = await self._run(self._ovs_vsctl, "list-br")
        except OvsdbError:
            return False
        return name in out.split()

    async def _port_exists(self, bridge: str, name: str) -> bool:
        try:
            out = await self._run(self._ovs_vsctl, "list-ports", bridge)
        except OvsdbError:
            return False
        return name in out.split()

    async def _run(self, *args: str) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise OvsdbError(f"{args[0]!r} not found on PATH") from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise OvsdbError(f"command timed out: {' '.join(args)}") from exc

        if proc.returncode != 0:
            raise OvsdbError(
                f"command failed ({proc.returncode}): {' '.join(args)}: "
                f"{stderr_b.decode(errors='replace').strip()}"
            )
        return stdout_b.decode()


# ---------------------------------------------------------------------------
# Parsers (small enough to stay alongside the adapter)
# ---------------------------------------------------------------------------


# OVS's typed-value encoding always uses a 2-element list:
# [tag, payload], where tag is one of "uuid"/"set"/"map".
_OVS_TYPED_LEN = 2


def _parse_version(raw: str) -> str | None:
    # ``ovs-vsctl --version`` → ``ovs-vsctl (Open vSwitch) 3.2.0\n...``
    head = raw.strip().splitlines()[0] if raw.strip() else ""
    parts = head.rsplit(" ", 1)
    return parts[-1] if len(parts) == _OVS_TYPED_LEN else None


def _parse_bridges(
    bridges_raw: str,
    ports_raw: str,
    ifaces_raw: str,
) -> list[BridgeState]:
    bridges_doc = json.loads(bridges_raw)
    ports_doc = json.loads(ports_raw)
    ifaces_doc = json.loads(ifaces_raw)

    ifaces_by_uuid = {
        _ovs_uuid(row[0]): InterfaceState(
            name=str(row[1]),
            type=str(row[2] or "internal"),
            options=_ovs_map(row[3]),
        )
        for row in ifaces_doc.get("data", [])
    }
    ports_by_uuid: dict[str, PortState] = {}
    for row in ports_doc.get("data", []):
        uuid_ = _ovs_uuid(row[0])
        name = str(row[1])
        tag = row[2] if isinstance(row[2], int) else None
        trunks_raw = row[3]
        trunks = tuple(int(t) for t in _ovs_set(trunks_raw))
        iface_uuids = [_ovs_uuid(u) for u in _ovs_set(row[4])]
        ports_by_uuid[uuid_] = PortState(
            name=name,
            tag=tag,
            trunks=trunks,
            interfaces=tuple(ifaces_by_uuid[u] for u in iface_uuids if u in ifaces_by_uuid),
        )

    bridges: list[BridgeState] = []
    for row in bridges_doc.get("data", []):
        name = str(row[0])
        datapath_type = str(row[1] or "system")
        port_uuids = [_ovs_uuid(u) for u in _ovs_set(row[2])]
        bridges.append(
            BridgeState(
                name=name,
                datapath_type=datapath_type,
                ports=tuple(ports_by_uuid[u] for u in port_uuids if u in ports_by_uuid),
            )
        )
    return bridges


def _is_tagged(value: Any, tag: str) -> bool:
    return isinstance(value, list) and len(value) == _OVS_TYPED_LEN and value[0] == tag


def _ovs_uuid(value: Any) -> str:
    # ovs-vsctl returns ["uuid", "<uuid>"]
    if _is_tagged(value, "uuid"):
        return str(value[1])
    return str(value)


def _ovs_set(value: Any) -> list[Any]:
    # OVS encodes sets as ["set", [...]] or a bare scalar for singletons.
    if _is_tagged(value, "set"):
        return list(value[1])
    if value in (None, ""):
        return []
    return [value]


def _ovs_map(value: Any) -> dict[str, str]:
    # OVS maps come back as ["map", [[k, v], ...]].
    if _is_tagged(value, "map"):
        return {str(k): str(v) for k, v in value[1]}
    return {}
