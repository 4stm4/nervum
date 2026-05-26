"""Isolated guest DB fallback for QEMU E2E tests.

The primary contract is the public HTTP API. This helper exists only for
N0 outbox/audit coverage on images that do not expose the read API yet.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any


class GuestDbInspector:
    def __init__(self) -> None:
        self.host = os.environ.get("E2E_QEMU_HOST", "rpi4-codex")
        self.user = os.environ.get("E2E_QEMU_USER") or os.environ.get("USER", "")
        self.ssh_port = os.environ.get("E2E_QEMU_SSH_PORT", "10022")
        self.db_path = os.environ.get(
            "E2E_QEMU_GUEST_DB_PATH",
            "/var/lib/sdn-controller/sdn_controller.db",
        )

    def available(self) -> bool:
        return bool(self.host)

    def fetch_outbox_event(self, resource_id: str) -> dict[str, Any] | None:
        sql = (
            "select json_object("
            "'event_id', event_id, "
            "'id', id, "
            "'event_type', event_type, "
            "'resource_type', resource_type, "
            "'resource_id', resource_id, "
            "'schema_version', schema_version, "
            "'project_id', project_id, "
            "'payload', payload"
            ") from outbox_events where resource_id = "
            f"{_sqlite_quote(resource_id)} order by event_id desc limit 1;"
        )
        command = (
            "sqlite3 -json "
            f"{shlex.quote(self.db_path)} "
            f"{shlex.quote(sql)}"
        )
        output = self._guest(command)
        if not output.strip():
            return None
        rows = json.loads(output)
        if not rows:
            return None
        return dict(json.loads(rows[0]["json_object(...)"]))

    def _guest(self, command: str) -> str:
        remote = f"{self.user}@{self.host}" if self.user else self.host
        nested = (
            "ssh -p "
            f"{shlex.quote(self.ssh_port)} "
            "-o BatchMode=yes -o StrictHostKeyChecking=no "
            "root@127.0.0.1 "
            f"{shlex.quote(command)}"
        )
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", remote, nested],
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout


def _sqlite_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
