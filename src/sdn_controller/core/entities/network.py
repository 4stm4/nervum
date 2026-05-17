"""Network aggregate (minimal milestone-1 shape).

The aggregate captures *intent* (desired state). Actual state, planning and
reconciliation live in later milestones (see Milestone 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from sdn_controller.core.value_objects.enums import NetworkType
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import NetworkId, SubnetId

# Reasonable VLAN/VNI bounds — enforced here so adapters can trust the entity.
_VLAN_MIN = 1
_VLAN_MAX = 4094
_VNI_MIN = 1
_VNI_MAX = 16_777_215  # 2^24 - 1
_MTU_MIN = 576
_MTU_MAX = 9216


@dataclass(slots=True)
class Subnet:
    id: SubnetId
    cidr: str
    gateway: str | None = None

    def __post_init__(self) -> None:
        try:
            net: IPv4Network | IPv6Network = ip_network(self.cidr, strict=True)
        except ValueError as exc:
            raise ValidationError(f"invalid cidr: {self.cidr}: {exc}") from exc

        if self.gateway is not None:
            try:
                gw = ip_address(self.gateway)
            except ValueError as exc:
                raise ValidationError(f"invalid gateway: {self.gateway}: {exc}") from exc
            if gw not in net:
                raise ValidationError(
                    f"gateway {self.gateway} is not inside subnet {self.cidr}",
                )


@dataclass(slots=True)
class Network:
    id: NetworkId
    name: str
    type: NetworkType
    created_at: datetime
    updated_at: datetime
    mtu: int = 1500
    vlan_id: int | None = None
    vni: int | None = None
    subnet: Subnet | None = None
    intent_version: int = 1
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate()

    # -- invariants --------------------------------------------------------

    def _validate(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("network name must be non-empty")

        if not (_MTU_MIN <= self.mtu <= _MTU_MAX):
            raise ValidationError(f"mtu {self.mtu} out of range [{_MTU_MIN}, {_MTU_MAX}]")

        match self.type:
            case NetworkType.FLAT:
                if self.vlan_id is not None or self.vni is not None:
                    raise ValidationError("flat network must not set vlan_id or vni")
            case NetworkType.VLAN:
                if self.vlan_id is None:
                    raise ValidationError("vlan network requires vlan_id")
                if self.vni is not None:
                    raise ValidationError("vlan network must not set vni")
                if not (_VLAN_MIN <= self.vlan_id <= _VLAN_MAX):
                    raise ValidationError(
                        f"vlan_id {self.vlan_id} out of range [{_VLAN_MIN}, {_VLAN_MAX}]"
                    )
            case NetworkType.VXLAN:
                if self.vni is None:
                    raise ValidationError("vxlan network requires vni")
                if self.vlan_id is not None:
                    raise ValidationError("vxlan network must not set vlan_id")
                if not (_VNI_MIN <= self.vni <= _VNI_MAX):
                    raise ValidationError(f"vni {self.vni} out of range [{_VNI_MIN}, {_VNI_MAX}]")

    # -- behaviour ---------------------------------------------------------

    def bump_intent(self, *, now: datetime) -> None:
        """Record a new desired-state revision (Milestone 5 will hash this)."""
        self.intent_version += 1
        self.updated_at = now
