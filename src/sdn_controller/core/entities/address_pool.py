"""AddressPool entity (N1-03).

An AddressPool is a named set of CIDRs / individual IP addresses that can be
referenced as source/destination operands in security policies (N2) or as
SecurityGroup members.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_network

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import AddressPoolId, ProjectId

_MAX_NAME_LEN = 255
_MAX_DESC_LEN = 512


def _validate_cidrs(cidrs: tuple[str, ...]) -> None:
    for cidr in cidrs:
        try:
            ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ValidationError(f"invalid CIDR in address pool: {cidr!r}: {exc}") from exc


@dataclass(slots=True)
class AddressPool:
    id: AddressPoolId
    name: str
    created_at: datetime
    updated_at: datetime
    description: str = ""
    project_id: ProjectId | None = None
    cidrs: tuple[str, ...] = ()
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("address pool name must be non-empty")
        if len(self.name) > _MAX_NAME_LEN:
            raise ValidationError(f"address pool name too long (max {_MAX_NAME_LEN})")
        if len(self.description) > _MAX_DESC_LEN:
            raise ValidationError(f"address pool description too long (max {_MAX_DESC_LEN})")
        _validate_cidrs(self.cidrs)

    def update(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        cidrs: tuple[str, ...] | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        if name is not None:
            if not name.strip():
                raise ValidationError("address pool name must be non-empty")
            self.name = name
        if description is not None:
            self.description = description
        if cidrs is not None:
            _validate_cidrs(cidrs)
            self.cidrs = tuple(cidrs)
        if labels is not None:
            self.labels = dict(labels)
        self.updated_at = now


__all__ = ["AddressPool"]
