"""SecurityGroup + SecurityGroupMember entities (N1-02).

A SecurityGroup is a named set of ports or addresses that can be referenced in
security policies (N2).  Members are added/removed independently via
SecurityGroupMember rows; the group itself just carries metadata.

Member types:

* ``logical_port`` — value is a LogicalPort id.
* ``cidr``         — value is an IP prefix, e.g. ``"10.0.0.0/24"``.
* ``address_pool`` — value is an AddressPool id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import ProjectId, SecurityGroupId

_VALID_MEMBER_TYPES: frozenset[str] = frozenset({"logical_port", "cidr", "address_pool"})
_MAX_NAME_LEN = 255
_MAX_DESC_LEN = 512


@dataclass(slots=True)
class SecurityGroup:
    id: SecurityGroupId
    name: str
    created_at: datetime
    updated_at: datetime
    description: str = ""
    project_id: ProjectId | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("security group name must be non-empty")
        if len(self.name) > _MAX_NAME_LEN:
            raise ValidationError(f"security group name too long (max {_MAX_NAME_LEN})")
        if len(self.description) > _MAX_DESC_LEN:
            raise ValidationError(f"security group description too long (max {_MAX_DESC_LEN})")

    def update(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        if name is not None:
            if not name.strip():
                raise ValidationError("security group name must be non-empty")
            if len(name) > _MAX_NAME_LEN:
                raise ValidationError(f"security group name too long (max {_MAX_NAME_LEN})")
            self.name = name
        if description is not None:
            if len(description) > _MAX_DESC_LEN:
                raise ValidationError(
                    f"security group description too long (max {_MAX_DESC_LEN})"
                )
            self.description = description
        if labels is not None:
            self.labels = dict(labels)
        self.updated_at = now


@dataclass(frozen=True, slots=True)
class SecurityGroupMember:
    """Join entity: one entry per (sg, type, value) triple."""

    sg_id: SecurityGroupId
    member_type: str   # "logical_port" | "cidr" | "address_pool"
    member_value: str  # port id, CIDR string, or pool id
    created_at: datetime

    def __post_init__(self) -> None:
        if self.member_type not in _VALID_MEMBER_TYPES:
            raise ValidationError(
                f"invalid member_type {self.member_type!r}; "
                f"must be one of {sorted(_VALID_MEMBER_TYPES)}"
            )
        if not self.member_value:
            raise ValidationError("member_value must be non-empty")


__all__ = ["SecurityGroup", "SecurityGroupMember"]
