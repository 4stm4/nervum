"""QosPolicy entity (N1-05).

A QoS policy defines bandwidth and DSCP marking constraints that can be
attached to a LogicalPort (N2+).

All rate fields are in kbps.  DSCP must be in [0, 63].  ``None`` means
"not limited" / "no marking".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import ProjectId, QosPolicyId

_DSCP_MIN = 0
_DSCP_MAX = 63
_MAX_NAME_LEN = 255
_MAX_DESC_LEN = 512


@dataclass(slots=True)
class QosPolicy:
    id: QosPolicyId
    name: str
    created_at: datetime
    updated_at: datetime
    description: str = ""
    project_id: ProjectId | None = None
    ingress_kbps: int | None = None
    egress_kbps: int | None = None
    burst_kb: int | None = None
    dscp: int | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("QoS policy name must be non-empty")
        if len(self.name) > _MAX_NAME_LEN:
            raise ValidationError(f"QoS policy name too long (max {_MAX_NAME_LEN})")
        if len(self.description) > _MAX_DESC_LEN:
            raise ValidationError(f"QoS policy description too long (max {_MAX_DESC_LEN})")
        self._validate_rates()

    def _validate_rates(self) -> None:
        for value, label in [
            (self.ingress_kbps, "ingress_kbps"),
            (self.egress_kbps, "egress_kbps"),
            (self.burst_kb, "burst_kb"),
        ]:
            if value is not None and value <= 0:
                raise ValidationError(f"{label} must be positive (got {value})")
        if self.dscp is not None and not (_DSCP_MIN <= self.dscp <= _DSCP_MAX):
            raise ValidationError(
                f"dscp {self.dscp} out of range [{_DSCP_MIN}, {_DSCP_MAX}]"
            )

    def update(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        ingress_kbps: int | None = None,
        egress_kbps: int | None = None,
        burst_kb: int | None = None,
        dscp: int | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        """Update mutable fields.  Pass ``None`` to leave a field unchanged."""
        if name is not None:
            if not name.strip():
                raise ValidationError("QoS policy name must be non-empty")
            self.name = name
        if description is not None:
            self.description = description
        if ingress_kbps is not None:
            self.ingress_kbps = ingress_kbps
        if egress_kbps is not None:
            self.egress_kbps = egress_kbps
        if burst_kb is not None:
            self.burst_kb = burst_kb
        if dscp is not None:
            self.dscp = dscp
        if labels is not None:
            self.labels = dict(labels)
        self._validate_rates()
        self.updated_at = now


__all__ = ["QosPolicy"]
