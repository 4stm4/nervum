"""``IpAllocation`` — a single IP handed out from a subnet to an owner.

We keep the entity narrow: identity, where it sits, who holds it, when it
was minted, and a free-form ``label``. The state machine is tiny — once an
allocation exists it stays until released, and release is a hard delete (we
don't keep tombstones). Audit + history will live in M10's audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import IpAllocationId, SubnetId
from sdn_controller.core.value_objects.ipam import IpAllocationKind, OwnerRef


@dataclass(slots=True)
class IpAllocation:
    id: IpAllocationId
    subnet_id: SubnetId
    ip_address: str
    owner: OwnerRef
    kind: IpAllocationKind
    allocated_at: datetime
    label: str | None = None

    def __post_init__(self) -> None:
        try:
            ip_address(self.ip_address)
        except ValueError as exc:
            raise ValidationError(f"invalid ip address: {self.ip_address}: {exc}") from exc
        if not self.owner.type or not self.owner.id:
            raise ValidationError("owner must have non-empty type and id")
