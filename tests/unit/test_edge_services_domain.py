"""Domain invariants for controller-side edge-service value objects."""

from __future__ import annotations

import pytest

from sdn_controller.core.value_objects.edge_services import (
    DhcpSpec,
    FirewallAction,
    FirewallPolicy,
    FirewallProto,
    FirewallRule,
    NatSpec,
)
from sdn_controller.core.value_objects.errors import ValidationError

# ---------------------------------------------------------------------------
# DhcpSpec
# ---------------------------------------------------------------------------


def test_dhcp_spec_accepts_valid_range() -> None:
    spec = DhcpSpec(range_start="10.0.0.10", range_end="10.0.0.250")
    assert spec.range_start == "10.0.0.10"


def test_dhcp_spec_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError, match="range_start must be <= range_end"):
        DhcpSpec(range_start="10.0.0.250", range_end="10.0.0.10")


def test_dhcp_spec_rejects_mixed_families() -> None:
    with pytest.raises(ValidationError, match="mixes address families"):
        DhcpSpec(range_start="10.0.0.1", range_end="::1")


def test_dhcp_spec_rejects_short_lease() -> None:
    with pytest.raises(ValidationError, match="lease_time_seconds"):
        DhcpSpec(range_start="10.0.0.1", range_end="10.0.0.2", lease_time_seconds=30)


def test_dhcp_spec_rejects_invalid_ip() -> None:
    with pytest.raises(ValidationError, match="invalid dhcp range"):
        DhcpSpec(range_start="not-an-ip", range_end="10.0.0.2")


# ---------------------------------------------------------------------------
# NatSpec
# ---------------------------------------------------------------------------


def test_nat_spec_accepts_iface_name() -> None:
    NatSpec(egress_interface="eth0")


def test_nat_spec_rejects_empty_iface() -> None:
    with pytest.raises(ValidationError, match="invalid egress_interface"):
        NatSpec(egress_interface="")


def test_nat_spec_rejects_iface_with_whitespace() -> None:
    with pytest.raises(ValidationError, match="invalid egress_interface"):
        NatSpec(egress_interface="eth 0")


# ---------------------------------------------------------------------------
# FirewallRule
# ---------------------------------------------------------------------------


def test_firewall_rule_with_valid_cidr_and_ports() -> None:
    rule = FirewallRule(
        action=FirewallAction.ACCEPT,
        proto=FirewallProto.TCP,
        source_cidr="10.0.0.0/24",
        destination_port_start=80,
        destination_port_end=443,
    )
    assert rule.destination_port_end == 443


def test_firewall_rule_rejects_bad_cidr() -> None:
    with pytest.raises(ValidationError, match="invalid source_cidr"):
        FirewallRule(source_cidr="not-a-cidr")


def test_firewall_rule_icmp_cannot_have_ports() -> None:
    with pytest.raises(ValidationError, match="icmp rules cannot carry ports"):
        FirewallRule(
            proto=FirewallProto.ICMP,
            destination_port_start=80,
        )


def test_firewall_rule_rejects_inverted_port_range() -> None:
    with pytest.raises(ValidationError, match="destination port end"):
        FirewallRule(
            proto=FirewallProto.TCP,
            destination_port_start=443,
            destination_port_end=80,
        )


def test_firewall_rule_rejects_out_of_range_ports() -> None:
    with pytest.raises(ValidationError, match="out of range"):
        FirewallRule(proto=FirewallProto.TCP, destination_port_start=70000)


# ---------------------------------------------------------------------------
# FirewallPolicy
# ---------------------------------------------------------------------------


def test_firewall_policy_default_drop_zero_rules() -> None:
    policy = FirewallPolicy()
    assert policy.default_action == FirewallAction.DROP
    assert policy.rules == ()


def test_firewall_policy_preserves_rule_order() -> None:
    rule_a = FirewallRule(action=FirewallAction.ACCEPT, proto=FirewallProto.TCP)
    rule_b = FirewallRule(action=FirewallAction.DROP, proto=FirewallProto.UDP)
    policy = FirewallPolicy(default_action=FirewallAction.DROP, rules=(rule_a, rule_b))
    assert policy.rules == (rule_a, rule_b)
