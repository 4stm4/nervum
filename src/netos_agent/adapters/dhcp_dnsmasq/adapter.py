"""Production DHCP via ``dnsmasq`` config drops.

Convention: one config fragment per scope at ``<config_dir>/sdn-<id>.conf``.
Apply steps:

1. **Generate** the fragment in a tmp file alongside the destination
   (same filesystem, so ``os.replace`` is atomic).
2. **Validate** with ``dnsmasq --test --conf-file=<tmp>``.
3. **Replace** the destination with ``os.replace``.
4. **Reload** dnsmasq with ``SIGHUP`` (or systemd if the operator
   prefers — set ``reload_command`` in settings).

We never edit dnsmasq.conf directly: the host's main config must include
``conf-dir=/etc/dnsmasq.d`` (the distro default). That keeps our drops
isolated and the host's master config intact.

Lease reading uses ``--dhcp-leasefile`` parsing (``/var/lib/misc/dnsmasq.leases``).
The format is ``<expiry> <mac> <ip> <hostname> <client-id>`` per line.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import tempfile
from collections.abc import Sequence
from ipaddress import ip_address, ip_network
from pathlib import Path

from netos_agent.core.value_objects.edge_services import DhcpScopeSpec
from netos_agent.core.value_objects.errors import (
    NotFoundError,
    OvsdbError,
    ValidationError,
)
from netos_agent.ports.dhcp import DhcpLease

_PREFIX = "sdn-"
_DEFAULT_TIMEOUT_S = 10.0
_LEASE_LINE_MIN_FIELDS = 4  # expiry mac ip hostname [client-id]


class DnsmasqDhcp:
    def __init__(
        self,
        *,
        dnsmasq: str = "dnsmasq",
        config_dir: Path | str = "/etc/dnsmasq.d",
        lease_file: Path | str = "/var/lib/misc/dnsmasq.leases",
        pid_file: Path | str = "/run/dnsmasq/dnsmasq.pid",
        reload_command: Sequence[str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._dnsmasq = dnsmasq
        self._config_dir = Path(config_dir)
        self._lease_file = Path(lease_file)
        self._pid_file = Path(pid_file)
        self._reload_command = tuple(reload_command) if reload_command else None
        self._timeout = timeout

    # -- DhcpPort ----------------------------------------------------------

    async def validate(self, scope: DhcpScopeSpec) -> None:
        rendered = self._render(scope)
        await self._validate_text(rendered)

    async def apply(self, scope: DhcpScopeSpec) -> bool:
        target = self._path_for(scope.scope_id)
        rendered = self._render(scope)
        existing = _safe_read(target)
        if existing == rendered:
            return False
        await self._validate_text(rendered)
        await asyncio.to_thread(_atomic_write, target, rendered)
        await self._reload()
        return True

    async def delete(self, scope_id: str) -> bool:
        target = self._path_for(scope_id)
        if not target.exists():
            return False
        await asyncio.to_thread(target.unlink)
        await self._reload()
        return True

    async def list_scopes(self) -> list[DhcpScopeSpec]:
        # Reading our own fragments back is a parse problem; for now we
        # surface the *contents* of the drop directory by deriving from
        # the filename and a single ``dhcp-range`` line. Operators who need
        # richer round-tripping should read the controller's intent, not
        # the agent's dump.
        result: list[DhcpScopeSpec] = []
        if not self._config_dir.exists():
            return result
        for path in sorted(self._config_dir.glob(f"{_PREFIX}*.conf")):
            scope = _parse_fragment(path)
            if scope is not None:
                result.append(scope)
        return result

    async def get_leases(self, scope_id: str) -> list[DhcpLease]:
        try:
            text = await asyncio.to_thread(self._lease_file.read_text, encoding="utf-8")
        except FileNotFoundError as exc:
            raise NotFoundError(f"lease file {self._lease_file} not found") from exc
        scope = self._path_for(scope_id)
        cidr_hint = _cidr_from_fragment(scope) if scope.exists() else None
        return [
            lease
            for lease in _parse_leases(text)
            if cidr_hint is None or _ip_in_cidr(lease.ip_address, cidr_hint)
        ]

    # -- internals ---------------------------------------------------------

    def _path_for(self, scope_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", scope_id)
        return self._config_dir / f"{_PREFIX}{safe}.conf"

    def _render(self, scope: DhcpScopeSpec) -> str:
        lines = [
            f"# managed by sdn-controller; scope_id={scope.scope_id}",
            f"# cidr={scope.cidr}",
        ]
        lines.append(
            f"dhcp-range={scope.range_start},{scope.range_end},{scope.lease_time_seconds}s"
        )
        if scope.gateway is not None:
            lines.append(f"dhcp-option={_tag_for(scope.scope_id)},3,{scope.gateway}")
        if scope.dns_servers:
            joined = ",".join(scope.dns_servers)
            lines.append(f"dhcp-option={_tag_for(scope.scope_id)},6,{joined}")
        if scope.domain_name is not None:
            lines.append(f"dhcp-option={_tag_for(scope.scope_id)},15,{scope.domain_name}")
        return "\n".join(lines) + "\n"

    async def _validate_text(self, text: str) -> None:
        if shutil.which(self._dnsmasq) is None:
            raise OvsdbError(f"{self._dnsmasq!r} not found on PATH")
        tmp_dir = await asyncio.to_thread(_mkdtemp)
        try:
            tmp = Path(tmp_dir) / "scope.conf"
            await asyncio.to_thread(tmp.write_text, text, "utf-8")
            await self._run(self._dnsmasq, "--test", f"--conf-file={tmp}")
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp_dir, True)

    async def _reload(self) -> None:
        if self._reload_command is not None:
            await self._run(*self._reload_command)
            return
        try:
            pid = int(self._pid_file.read_text(encoding="utf-8").strip())
        except FileNotFoundError as exc:
            raise OvsdbError(f"dnsmasq pid file {self._pid_file} missing") from exc
        except ValueError as exc:
            raise OvsdbError(f"dnsmasq pid file {self._pid_file} malformed") from exc
        try:
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError as exc:
            raise OvsdbError(f"dnsmasq pid {pid} not running") from exc
        except PermissionError as exc:
            raise OvsdbError(f"not allowed to signal dnsmasq pid {pid}") from exc

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
# Module helpers — small + pure so they're easy to unit-test in isolation.
# ---------------------------------------------------------------------------


def _tag_for(scope_id: str) -> str:
    return f"tag:{_PREFIX}{re.sub(r'[^A-Za-z0-9]', '', scope_id)}"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _safe_read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _mkdtemp() -> str:
    return tempfile.mkdtemp(prefix="sdn-dnsmasq-")


_FRAGMENT_RX = re.compile(
    r"^dhcp-range\s*=\s*([^,]+),\s*([^,]+),\s*(\d+)\s*s?\s*$",
    re.MULTILINE,
)
_CIDR_RX = re.compile(r"^# cidr=([0-9./]+)\s*$", re.MULTILINE)
_ID_RX = re.compile(r"^# managed by sdn-controller; scope_id=(\S+)\s*$", re.MULTILINE)


def _parse_fragment(path: Path) -> DhcpScopeSpec | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRAGMENT_RX.search(text)
    cidr = _CIDR_RX.search(text)
    id_m = _ID_RX.search(text)
    if not (m and cidr and id_m):
        return None
    try:
        return DhcpScopeSpec(
            scope_id=id_m.group(1),
            cidr=cidr.group(1),
            range_start=m.group(1).strip(),
            range_end=m.group(2).strip(),
            lease_time_seconds=int(m.group(3)),
        )
    except ValidationError:
        return None


def _cidr_from_fragment(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _CIDR_RX.search(text)
    return m.group(1) if m else None


def _ip_in_cidr(addr: str, cidr: str) -> bool:
    try:
        return ip_address(addr) in ip_network(cidr, strict=False)
    except ValueError:
        return False


def _parse_leases(text: str) -> list[DhcpLease]:
    leases: list[DhcpLease] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < _LEASE_LINE_MIN_FIELDS:
            continue
        try:
            expiry = int(parts[0])
        except ValueError:
            continue
        hostname = parts[3] if parts[3] != "*" else None
        leases.append(
            DhcpLease(
                ip_address=parts[2],
                mac_address=parts[1],
                hostname=hostname,
                expires_at_epoch=expiry,
            )
        )
    return leases
