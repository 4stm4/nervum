"""Shared value objects for the edge-service stack (DHCP / DNS / NAT / firewall).

These live alongside ``plan.py`` because every variant is referenced from
both the wire (plan steps) and the read-side (current state listings).
Keeping them in one module also means the controller's mirror has a single
shape to copy — there's no temptation to invent a parallel grammar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from ipaddress import ip_address, ip_network

from netos_agent.core.value_objects.errors import ValidationError

# Magic-number guard rails — extracted so ruff's PLR2004 stays quiet and the
# bounds have an obvious name in error messages.
_MIN_DHCP_LEASE_SECONDS = 60
_MIN_PORT = 1
_MAX_PORT = 65535


# ---------------------------------------------------------------------------
# DHCP
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DhcpScopeSpec:
    """A single DHCP pool we want dnsmasq (or Kea) to serve.

    ``scope_id`` is the controller's stable identifier for the pool — the
    agent uses it as the config filename so it can drop the right fragment
    when the scope is removed. ``cidr``, ``range_start`` and ``range_end``
    are validated together: range must be inside the CIDR and ascending.
    """

    scope_id: str
    cidr: str
    range_start: str
    range_end: str
    gateway: str | None = None
    dns_servers: tuple[str, ...] = ()
    lease_time_seconds: int = 3600
    domain_name: str | None = None

    def __post_init__(self) -> None:
        try:
            net = ip_network(self.cidr, strict=True)
        except ValueError as exc:
            raise ValidationError(f"invalid cidr: {self.cidr}: {exc}") from exc
        try:
            start = ip_address(self.range_start)
            end = ip_address(self.range_end)
        except ValueError as exc:
            raise ValidationError(f"invalid dhcp range bound: {exc}") from exc
        if start not in net or end not in net:
            raise ValidationError(
                f"dhcp range {self.range_start}-{self.range_end} is outside subnet {self.cidr}",
            )
        if int(start) > int(end):
            raise ValidationError("dhcp range start must be <= end")
        if self.gateway is not None:
            try:
                gw = ip_address(self.gateway)
            except ValueError as exc:
                raise ValidationError(f"invalid gateway: {self.gateway}: {exc}") from exc
            if gw not in net:
                raise ValidationError("dhcp gateway must be inside the CIDR")
        for srv in self.dns_servers:
            try:
                ip_address(srv)
            except ValueError as exc:
                raise ValidationError(f"invalid dns server: {srv}: {exc}") from exc
        if self.lease_time_seconds < _MIN_DHCP_LEASE_SECONDS:
            raise ValidationError(
                f"dhcp lease_time_seconds must be >= {_MIN_DHCP_LEASE_SECONDS}",
            )


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DnsRecord:
    """A single forward record. We only support A/AAAA/CNAME in M7."""

    name: str
    type: str  # "A" / "AAAA" / "CNAME"
    value: str
    ttl_seconds: int = 300

    def __post_init__(self) -> None:
        if self.type not in {"A", "AAAA", "CNAME"}:
            raise ValidationError(f"unsupported dns record type: {self.type}")
        if not self.name or "/" in self.name or "\n" in self.name:
            raise ValidationError(f"invalid dns record name: {self.name!r}")
        if self.type in {"A", "AAAA"}:
            try:
                ip_address(self.value)
            except ValueError as exc:
                raise ValidationError(f"{self.type} record value must be an IP: {exc}") from exc


@dataclass(frozen=True, slots=True)
class DnsZoneSpec:
    """One authoritative zone served by the agent.

    ``zone`` is the FQDN of the zone (always with trailing dot internally
    so zone-file rendering is unambiguous). ``records`` carries the forward
    records; reverse records can be derived later from allocations and a
    PTR convention.
    """

    zone: str
    records: tuple[DnsRecord, ...] = ()
    soa_email: str = "hostmaster.invalid."

    def __post_init__(self) -> None:
        if not self.zone or self.zone.endswith("..") or " " in self.zone:
            raise ValidationError(f"invalid zone name: {self.zone!r}")


# ---------------------------------------------------------------------------
# NAT
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NatRuleSpec:
    """Source-NAT one tenant prefix out a specific WAN interface.

    The classic edge case: a tenant subnet (e.g. ``10.100.0.0/24``) is
    masqueraded behind a host's egress NIC (e.g. ``wan0``) so VMs can reach
    the Internet. ``rule_id`` is the controller's stable identifier so the
    agent can find and remove it later.
    """

    rule_id: str
    source_cidr: str
    egress_interface: str

    def __post_init__(self) -> None:
        try:
            ip_network(self.source_cidr, strict=True)
        except ValueError as exc:
            raise ValidationError(f"invalid source_cidr: {self.source_cidr}: {exc}") from exc
        if not self.egress_interface or " " in self.egress_interface:
            raise ValidationError(f"invalid egress_interface: {self.egress_interface!r}")


# ---------------------------------------------------------------------------
# Firewall policy
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
class FirewallRuleSpec:
    """Single allow/deny rule, evaluated in declaration order."""

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
class FirewallPolicySpec:
    """Top-level policy: a default action plus an ordered list of rules.

    Default-deny is what the plan asks for at the tenant boundary; allow
    rules carve safe holes. The policy is bound to a network via
    ``policy_id`` (typically ``network_id``) so the agent can apply or
    delete the right table.
    """

    policy_id: str
    default_action: FirewallAction = FirewallAction.DROP
    rules: tuple[FirewallRuleSpec, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.policy_id or " " in self.policy_id:
            raise ValidationError(f"invalid policy_id: {self.policy_id!r}")
