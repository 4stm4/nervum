"""``OvsState`` — read-only snapshot of local OVS.

The aggregate is *content-addressable*: ``state.hash`` is a SHA-256 over a
canonical JSON encoding of the same data. The controller compares hashes to
detect drift without re-fetching the full state and so two readers always
agree on equality regardless of dict ordering.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class InterfaceState:
    name: str
    type: str = "system"  # system / internal / vxlan / patch / ...
    options: dict[str, str] = field(default_factory=dict)
    admin_state: str = "up"

    def to_canonical(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "options": dict(sorted(self.options.items())),
            "admin_state": self.admin_state,
        }


@dataclass(frozen=True, slots=True)
class PortState:
    name: str
    interfaces: tuple[InterfaceState, ...] = ()
    tag: int | None = None
    trunks: tuple[int, ...] = ()
    external_ids: dict[str, str] = field(default_factory=dict)

    def to_canonical(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tag": self.tag,
            "trunks": sorted(self.trunks),
            "external_ids": dict(sorted(self.external_ids.items())),
            "interfaces": [i.to_canonical() for i in sorted(self.interfaces, key=lambda i: i.name)],
        }


@dataclass(frozen=True, slots=True)
class BridgeState:
    name: str
    datapath_type: str = "system"
    ports: tuple[PortState, ...] = ()
    external_ids: dict[str, str] = field(default_factory=dict)

    def to_canonical(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "datapath_type": self.datapath_type,
            "external_ids": dict(sorted(self.external_ids.items())),
            "ports": [p.to_canonical() for p in sorted(self.ports, key=lambda p: p.name)],
        }


@dataclass(frozen=True, slots=True)
class OvsState:
    ovs_version: str | None = None
    bridges: tuple[BridgeState, ...] = ()

    def to_canonical(self) -> dict[str, Any]:
        return {
            "ovs_version": self.ovs_version,
            "bridges": [b.to_canonical() for b in sorted(self.bridges, key=lambda b: b.name)],
        }

    @property
    def hash(self) -> str:
        encoded = json.dumps(
            self.to_canonical(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def find_bridge(self, name: str) -> BridgeState | None:
        for b in self.bridges:
            if b.name == name:
                return b
        return None
