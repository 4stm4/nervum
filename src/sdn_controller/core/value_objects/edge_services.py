"""Controller-side domain values for edge services (DHCP / DNS / NAT / FW).

These live on the aggregates (``Subnet.dhcp``, ``Network.nat`` …) and
carry only *intent* — none of the wire-level identifiers the agent needs.
The diff engine maps each intent into the matching ``Ensure*Step`` with
a scope/policy/rule id derived from the parent network's id, so renames
of agent-side objects stay stable across reconciles.

Mirrors the agent's shape only as far as the wire requires; we keep the
controller's vocabulary slightly smaller (e.g. ``DhcpSpec`` without
``scope_id``) so users of the controller's REST API don't have to think
about agent internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from ipaddress import ip_address, ip_network

from sdn_controller.core.value_objects.errors import ValidationError

_MIN_DHCP_LEASE_SECONDS = 60
_MIN_PORT = 1
_MAX_PORT = 65535


# ---------------------------------------------------------------------------
# DHCP
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DhcpSpec:
    """DHCP intent attached to a subnet."""

    range_start: str
    range_end: str
    lease_time_seconds: int = 3600
    domain_name: str | None = None

    def __post_init__(self) -> None:
        try:
            start = ip_address(self.range_start)
            end = ip_address(self.range_end)
        except ValueError as exc:
            raise ValidationError(f"invalid dhcp range: {exc}") from exc
        if type(start) is not type(end):
            raise ValidationError("dhcp range mixes address families")
        if int(start) > int(end):
            raise ValidationError("dhcp range_start must be <= range_end")
        if self.lease_time_seconds < _MIN_DHCP_LEASE_SECONDS:
            raise ValidationError(
                f"dhcp lease_time_seconds must be >= {_MIN_DHCP_LEASE_SECONDS}",
            )


# ---------------------------------------------------------------------------
# NAT
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NatSpec:
    """Source-NAT for the whole network out a single egress interface."""

    egress_interface: str

    def __post_init__(self) -> None:
        if not self.egress_interface or " " in self.egress_interface:
            raise ValidationError(f"invalid egress_interface: {self.egress_interface!r}")


# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------


class FirewallAction(StrEnum):
    ACCEPT = "accept"
    DROP = "drop"


class FirewallProto(StrEnum):
    ANY = "any"
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"


@dataclass(frozen=True, slots=True)
class FirewallRule:
    action: FirewallAction = FirewallAction.ACCEPT
    proto: FirewallProto = FirewallProto.ANY
    source_cidr: str | None = None
    destination_cidr: str | None = None
    destination_port_start: int | None = None
    destination_port_end: int | None = None

    def __post_init__(self) -> None:
        for cidr_field, value in (
            ("source_cidr", self.source_cidr),
            ("destination_cidr", self.destination_cidr),
        ):
            if value is None:
                continue
            try:
                ip_network(value, strict=False)
            except ValueError as exc:
                raise ValidationError(f"invalid {cidr_field}: {value}: {exc}") from exc
        if self.proto == FirewallProto.ICMP and (
            self.destination_port_start is not None or self.destination_port_end is not None
        ):
            raise ValidationError("icmp rules cannot carry ports")
        if self.destination_port_start is not None or self.destination_port_end is not None:
            start = self.destination_port_start or 0
            end = self.destination_port_end or start
            if not (_MIN_PORT <= start <= _MAX_PORT) or not (_MIN_PORT <= end <= _MAX_PORT):
                raise ValidationError(
                    f"destination port out of range [{_MIN_PORT}, {_MAX_PORT}]",
                )
            if end < start:
                raise ValidationError("destination port end must be >= start")


@dataclass(frozen=True, slots=True)
class FirewallPolicy:
    """Default action + ordered list of allow/deny rules.

    Default ``DROP`` is what the plan asks for at the tenant boundary;
    explicit ``rules`` carve safe holes.
    """

    default_action: FirewallAction = FirewallAction.DROP
    rules: tuple[FirewallRule, ...] = field(default_factory=tuple)
