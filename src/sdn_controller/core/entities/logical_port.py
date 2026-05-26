"""LogicalPort entity (N1-01).

A logical port is the binding point between a virtual network interface on a
Node and a Network.  It tracks the MAC/IP assignment and the attach status of
the VIF (tap interface, veth pair, etc.).

Status lifecycle::

    pending → active   (attach)
    active  → detached (detach)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_address as _parse_ip

from sdn_controller.core.value_objects.enums import LogicalPortStatus
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import LogicalPortId, NetworkId, NodeId, ProjectId

_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_MAX_NAME_LEN = 255


@dataclass(slots=True)
class LogicalPort:
    id: LogicalPortId
    name: str
    node_id: NodeId
    network_id: NetworkId
    created_at: datetime
    updated_at: datetime
    # Optional VIF identity on the host — e.g. "tapXXXX" or "veth0".
    vif_id: str | None = None
    mac_address: str | None = None   # normalised to lowercase "aa:bb:cc:dd:ee:ff"
    ip_address: str | None = None    # manually assigned or IPAM-allocated
    status: LogicalPortStatus = LogicalPortStatus.PENDING
    project_id: ProjectId | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("logical port name must be non-empty")
        if len(self.name) > _MAX_NAME_LEN:
            raise ValidationError(f"logical port name too long (max {_MAX_NAME_LEN})")
        if self.mac_address is not None:
            mac = self.mac_address.lower().strip()
            if not _MAC_RE.match(mac):
                raise ValidationError(
                    f"mac_address must be lowercase hex colon-separated (aa:bb:cc:dd:ee:ff): "
                    f"{self.mac_address!r}"
                )
            self.mac_address = mac
        if self.ip_address is not None:
            try:
                _parse_ip(self.ip_address)
            except ValueError as exc:
                raise ValidationError(
                    f"invalid ip_address: {self.ip_address}: {exc}"
                ) from exc

    # -- behaviour -----------------------------------------------------------

    def attach(self, *, vif_id: str | None = None, now: datetime) -> None:
        """Mark port as active (VIF connected)."""
        self.status = LogicalPortStatus.ACTIVE
        if vif_id is not None:
            self.vif_id = vif_id
        self.updated_at = now

    def detach(self, *, now: datetime) -> None:
        """Mark port as detached (VIF removed)."""
        self.status = LogicalPortStatus.DETACHED
        self.vif_id = None
        self.updated_at = now

    def update(
        self,
        *,
        name: str | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        if name is not None:
            if not name.strip():
                raise ValidationError("logical port name must be non-empty")
            self.name = name
        if labels is not None:
            self.labels = dict(labels)
        self.updated_at = now


__all__ = ["LogicalPort"]
