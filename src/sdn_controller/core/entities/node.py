"""Node entity.

The aggregate models a managed device throughout its lifecycle:

* **pending** — registered by an operator, no agent has connected yet
* **online** — fresh heartbeat (within the stale window)
* **stale**  — last heartbeat is older than the stale threshold
* **offline** — last heartbeat is older than the offline threshold
* **draining** — being decommissioned, do not schedule new workloads

State transitions happen through explicit methods (``register_seen``,
``mark_drain``); the *effective* status — what readers want — is computed by
``sdn_controller.core.services.node_status.derived_status`` from the
persisted status and ``last_seen_at`` so we don't need a background reaper
just to flip ``online`` → ``stale``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_address

from sdn_controller.core.value_objects.capabilities import NodeCapabilities
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
    capabilities: NodeCapabilities | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("node name must be non-empty")
        try:
            ip_address(self.mgmt_ip)
        except ValueError as exc:
            raise ValidationError(f"invalid mgmt_ip: {self.mgmt_ip}: {exc}") from exc

    # -- behaviour ---------------------------------------------------------

    def enroll(
        self,
        *,
        now: datetime,
        agent_version: str | None = None,
        capabilities: NodeCapabilities | None = None,
    ) -> None:
        """Transition from ``pending`` to ``online`` after agent connects."""
        if self.status is not NodeStatus.PENDING:
            raise ValidationError(
                f"node {self.id} is not pending (status={self.status.value}); "
                "enrolment is only valid for pending nodes",
            )
        self.status = NodeStatus.ONLINE
        self.last_seen_at = now
        self.updated_at = now
        if agent_version is not None:
            self.agent_version = agent_version
        if capabilities is not None:
            self.capabilities = capabilities

    def record_heartbeat(
        self,
        *,
        now: datetime,
        agent_version: str | None = None,
        capabilities: NodeCapabilities | None = None,
    ) -> None:
        """Update freshness/capabilities reported by the agent.

        The heartbeat does not auto-enroll a ``pending`` node: an operator
        explicitly enrolls via the token flow. From ``stale``/``offline`` the
        heartbeat does promote back to ``online`` — that's a recovery event,
        not a privileged decision.
        """
        if self.status is NodeStatus.PENDING:
            raise ValidationError(
                f"node {self.id} is still pending; agent must enroll before heartbeating",
            )
        self.last_seen_at = now
        self.updated_at = now
        if agent_version is not None:
            self.agent_version = agent_version
        if capabilities is not None:
            self.capabilities = capabilities
        if self.status in {NodeStatus.STALE, NodeStatus.OFFLINE}:
            self.status = NodeStatus.ONLINE

    def mark_drain(self, *, now: datetime) -> None:
        self.status = NodeStatus.DRAINING
        self.updated_at = now
