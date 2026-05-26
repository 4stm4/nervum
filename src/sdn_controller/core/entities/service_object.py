"""ServiceObject entity (N1-04).

A ServiceObject is a named protocol/port definition that can be referenced in
security policies (N2) as the "service" operand (i.e. what traffic).

Port specs follow the format ``"<port>"`` or ``"<start>-<end>"``, e.g.
``["80", "443", "8000-8080"]``.  Ports are only valid for ``tcp`` and ``udp``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import ProjectId, ServiceObjectId

_VALID_PROTOCOLS: frozenset[str] = frozenset({"tcp", "udp", "icmp", "any"})
_PORT_RANGE_RE = re.compile(r"^\d+(-\d+)?$")
_MAX_PORT = 65535
_MAX_NAME_LEN = 255
_MAX_DESC_LEN = 512


def _validate_port_specs(ports: tuple[str, ...], protocol: str) -> None:
    if ports and protocol in ("icmp", "any"):
        raise ValidationError(
            f"ports cannot be specified for protocol {protocol!r}; "
            "use 'tcp' or 'udp'"
        )
    for spec in ports:
        if not _PORT_RANGE_RE.match(spec):
            raise ValidationError(
                f"invalid port spec {spec!r}; use '80' or '8000-9000'"
            )
        parts = spec.split("-")
        lo = int(parts[0])
        hi = int(parts[1]) if len(parts) == 2 else lo
        if lo < 1 or hi > _MAX_PORT:
            raise ValidationError(f"port {spec!r} out of range [1, {_MAX_PORT}]")
        if len(parts) == 2 and lo >= hi:
            raise ValidationError(
                f"invalid port range {spec!r}: start must be strictly less than end"
            )


@dataclass(slots=True)
class ServiceObject:
    id: ServiceObjectId
    name: str
    protocol: str   # "tcp" | "udp" | "icmp" | "any"
    created_at: datetime
    updated_at: datetime
    description: str = ""
    project_id: ProjectId | None = None
    ports: tuple[str, ...] = ()   # only for tcp/udp
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("service object name must be non-empty")
        if len(self.name) > _MAX_NAME_LEN:
            raise ValidationError(f"service object name too long (max {_MAX_NAME_LEN})")
        if len(self.description) > _MAX_DESC_LEN:
            raise ValidationError(f"service object description too long (max {_MAX_DESC_LEN})")
        if self.protocol not in _VALID_PROTOCOLS:
            raise ValidationError(
                f"invalid protocol {self.protocol!r}; "
                f"must be one of {sorted(_VALID_PROTOCOLS)}"
            )
        _validate_port_specs(self.ports, self.protocol)

    def update(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        ports: tuple[str, ...] | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        if name is not None:
            if not name.strip():
                raise ValidationError("service object name must be non-empty")
            self.name = name
        if description is not None:
            self.description = description
        if ports is not None:
            _validate_port_specs(ports, self.protocol)
            self.ports = tuple(ports)
        if labels is not None:
            self.labels = dict(labels)
        self.updated_at = now


__all__ = ["ServiceObject"]
