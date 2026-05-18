"""Production DNS via CoreDNS zone files.

Convention:

* one zone file per controller-owned zone at ``<zones_dir>/db.<zone>``;
* a single managed Corefile drop at ``<corefile_dir>/sdn.conf`` that
  ``import``-s every ``db.<zone>``.

CoreDNS reload is signalled with ``SIGUSR1`` (or by writing a config
mtime — but signal is more portable). Zone-file syntax validation is
done with ``coredns -plugins`` available since 1.6: we run
``coredns -conf <tmp_corefile> -dns.port :0 -dns.tcp:0 -alsologtostderr=false``
as a dry parse. ``-dns.port :0`` makes it pick a free port; we kill the
process after a couple of seconds. That's the cleanest way to fail fast on a
malformed zone without standing up a real listener.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import signal
import tempfile
from pathlib import Path

from netos_agent.core.value_objects.edge_services import DnsRecord, DnsZoneSpec
from netos_agent.core.value_objects.errors import (
    NotFoundError,
    OvsdbError,
)

_VALIDATE_TIMEOUT_S = 5.0
_LOG = logging.getLogger(__name__)


class CorednsDns:
    def __init__(
        self,
        *,
        coredns: str = "coredns",
        zones_dir: Path | str = "/etc/coredns/zones",
        corefile_path: Path | str = "/etc/coredns/sdn-Corefile",
        pid_file: Path | str = "/run/coredns/coredns.pid",
    ) -> None:
        self._coredns = coredns
        self._zones_dir = Path(zones_dir)
        self._corefile_path = Path(corefile_path)
        self._pid_file = Path(pid_file)

    # -- DnsPort -----------------------------------------------------------

    async def validate(self, zone: DnsZoneSpec) -> None:
        if shutil.which(self._coredns) is None:
            raise OvsdbError(f"{self._coredns!r} not found on PATH")
        rendered = _render_zone(zone)
        corefile = _render_corefile_for_validation(zone)
        tmp_dir = await asyncio.to_thread(_mkdtemp)
        try:
            tmp_zone = Path(tmp_dir) / _zone_filename(zone.zone)
            tmp_corefile = Path(tmp_dir) / "Corefile"
            await asyncio.to_thread(tmp_zone.write_text, rendered, "utf-8")
            await asyncio.to_thread(tmp_corefile.write_text, corefile, "utf-8")
            await self._coredns_parse_check(tmp_corefile)
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp_dir, True)

    async def apply(self, zone: DnsZoneSpec) -> bool:
        rendered = _render_zone(zone)
        target = self._zone_path(zone.zone)
        existing = _safe_read(target)
        if existing == rendered:
            return False
        await self.validate(zone)
        await asyncio.to_thread(_atomic_write, target, rendered)
        await self._update_corefile()
        await self._reload()
        return True

    async def delete(self, zone: str) -> bool:
        target = self._zone_path(zone)
        if not target.exists():
            return False
        await asyncio.to_thread(target.unlink)
        await self._update_corefile()
        await self._reload()
        return True

    async def list_zones(self) -> list[DnsZoneSpec]:
        if not self._zones_dir.exists():
            return []
        out: list[DnsZoneSpec] = []
        for path in sorted(self._zones_dir.glob("db.*")):
            zone = _parse_zone(path)
            if zone is not None:
                out.append(zone)
        return out

    async def resolve_check(self, zone: str, name: str) -> str | None:
        path = self._zone_path(zone)
        parsed = _parse_zone(path) if path.exists() else None
        if parsed is None:
            return None
        for rec in parsed.records:
            if rec.name == name and rec.type in {"A", "AAAA"}:
                return rec.value
        return None

    # -- internals ---------------------------------------------------------

    def _zone_path(self, zone: str) -> Path:
        return self._zones_dir / _zone_filename(zone)

    async def _update_corefile(self) -> None:
        await asyncio.to_thread(self._zones_dir.mkdir, mode=0o755, exist_ok=True, parents=True)
        zones = sorted(p.name for p in self._zones_dir.glob("db.*"))
        body = _render_corefile_body(self._zones_dir, zones)
        existing = _safe_read(self._corefile_path)
        if existing == body:
            return
        await asyncio.to_thread(_atomic_write, self._corefile_path, body)

    async def _coredns_parse_check(self, corefile: Path) -> None:
        proc = await asyncio.create_subprocess_exec(
            self._coredns,
            "-conf",
            str(corefile),
            "-dns.port",
            ":0",  # ephemeral port — we don't need a real listener
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            # CoreDNS exits non-zero immediately on parse errors. If the
            # parse succeeded it will start listening — kill it after a
            # short grace period.
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=_VALIDATE_TIMEOUT_S
                )
            except TimeoutError:
                proc.send_signal(signal.SIGTERM)
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=_VALIDATE_TIMEOUT_S
                    )
                except TimeoutError as exc:
                    proc.kill()
                    await proc.wait()
                    raise OvsdbError("coredns validate hung") from exc
                # Successful start + SIGTERM is a "parse OK" outcome.
                return
            if proc.returncode != 0:
                raise OvsdbError(
                    "coredns rejected zone config: "
                    f"{stderr_b.decode(errors='replace').strip() or stdout_b.decode().strip()}"
                )
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def _reload(self) -> None:
        try:
            pid = int(self._pid_file.read_text(encoding="utf-8").strip())
        except FileNotFoundError as exc:
            raise NotFoundError(f"coredns pid file {self._pid_file} missing") from exc
        except ValueError as exc:
            raise OvsdbError(f"coredns pid file {self._pid_file} malformed") from exc
        try:
            os.kill(pid, signal.SIGUSR1)
        except ProcessLookupError as exc:
            raise OvsdbError(f"coredns pid {pid} not running") from exc
        except PermissionError as exc:
            raise OvsdbError(f"not allowed to signal coredns pid {pid}") from exc


# ---------------------------------------------------------------------------
# Render / parse helpers
# ---------------------------------------------------------------------------


def _zone_filename(zone: str) -> str:
    safe = zone.rstrip(".")
    return f"db.{safe}"


def _render_zone(zone: DnsZoneSpec) -> str:
    zone_fqdn = zone.zone if zone.zone.endswith(".") else zone.zone + "."
    lines = [
        f"; managed by sdn-controller; zone={zone.zone}",
        f"$ORIGIN {zone_fqdn}",
        "$TTL 300",
        f"@ IN SOA ns1.{zone_fqdn} {zone.soa_email} ("
        " 1 ; serial\n 3600 ; refresh\n 600 ; retry\n 604800 ; expire\n 300 ; minimum\n)",
        f"@ IN NS ns1.{zone_fqdn}",
    ]
    for rec in zone.records:
        lines.append(f"{rec.name} {rec.ttl_seconds} IN {rec.type} {rec.value}")
    return "\n".join(lines) + "\n"


def _render_corefile_body(zones_dir: Path, zone_files: list[str]) -> str:
    lines = ["# managed by sdn-controller"]
    for fname in zone_files:
        zone_name = fname.removeprefix("db.")
        lines.append(f"{zone_name}:53 {{\n    file {zones_dir}/{fname}\n    log\n    errors\n}}\n")
    return "\n".join(lines) + ("\n" if lines else "")


def _render_corefile_for_validation(zone: DnsZoneSpec) -> str:
    zone_name = zone.zone.rstrip(".")
    return f"{zone_name}:53 {{\n    file db.{zone_name}\n    errors\n}}\n"


_RECORD_RX = re.compile(
    r"^(?P<name>\S+)\s+(?P<ttl>\d+)\s+IN\s+(?P<type>A|AAAA|CNAME)\s+(?P<value>\S+)$",
    re.MULTILINE,
)
_ZONE_HEADER_RX = re.compile(r"^; managed by sdn-controller; zone=(\S+)\s*$", re.MULTILINE)


def _parse_zone(path: Path) -> DnsZoneSpec | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    header = _ZONE_HEADER_RX.search(text)
    if not header:
        return None
    records: list[DnsRecord] = []
    for m in _RECORD_RX.finditer(text):
        try:
            records.append(
                DnsRecord(
                    name=m.group("name"),
                    type=m.group("type"),
                    value=m.group("value"),
                    ttl_seconds=int(m.group("ttl")),
                )
            )
        except (ValueError, TypeError):
            _LOG.debug("skipping malformed dns record line: %s", m.group(0))
            continue
    try:
        return DnsZoneSpec(zone=header.group(1), records=tuple(records))
    except (ValueError, TypeError):
        _LOG.debug("malformed coredns zone header: %s", header.group(1))
        return None


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
    return tempfile.mkdtemp(prefix="sdn-coredns-")
