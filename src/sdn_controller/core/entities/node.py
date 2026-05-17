"""Node entity (minimal milestone-1 shape).

The full enrolment lifecycle, capability negotiation and heartbeat handling
arrive in Milestone 2. For now we expose just enough to list nodes through
the API and reference them from operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_address

from sdn_controller.core.value_objects.enums import NodeStatus
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import NodeId


@dataclass(slots=True)
class Node:
    id: NodeId
    name: str
    mgmt_ip: str
    created_at: datetime
    updated_at: datetime
    status: NodeStatus = NodeStatus.PENDING
    roles: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    agent_version: str | None = None
    last_seen_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("node name must be non-empty")
        try:
            ip_address(self.mgmt_ip)
        except ValueError as exc:
            raise ValidationError(f"invalid mgmt_ip: {self.mgmt_ip}: {exc}") from exc

    def mark_seen(self, *, now: datetime, agent_version: str | None = None) -> None:
        self.last_seen_at = now
        self.updated_at = now
        if agent_version is not None:
            self.agent_version = agent_version
        # Promotion from pending → online happens through the enrolment flow,
        # which runs in Milestone 2. Heartbeat must not silently enroll a node.
        if self.status in {NodeStatus.STALE, NodeStatus.OFFLINE}:
            self.status = NodeStatus.ONLINE
