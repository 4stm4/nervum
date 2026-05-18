"""Idempotency + listing for the in-memory edge-service adapters.

These adapters are what unit tests of ``ApplyPlan`` and integration tests
of the controller depend on. We pin behaviour: apply returns ``True`` only
on first call, ``False`` on subsequent identical applies, ``True`` again
when the spec changes.
"""

from __future__ import annotations

from netos_agent.adapters.dhcp_fake import FakeDhcp
from netos_agent.adapters.dns_fake import FakeDns
from netos_agent.adapters.firewall_fake import FakeFirewall
from netos_agent.core.value_objects.edge_services import (
    DhcpScopeSpec,
    DnsRecord,
    DnsZoneSpec,
    FirewallAction,
    FirewallPolicySpec,
    FirewallProto,
    FirewallRuleSpec,
    NatRuleSpec,
)

# ---------------------------------------------------------------------------
# DHCP
# ---------------------------------------------------------------------------


async def test_dhcp_apply_is_idempotent() -> None:
    dhcp = FakeDhcp()
    spec = DhcpScopeSpec(
        scope_id="scope-1",
        cidr="10.0.0.0/24",
        range_start="10.0.0.10",
        range_end="10.0.0.50",
    )

    first = await dhcp.apply(spec)
    second = await dhcp.apply(spec)

    assert first is True
    assert second is False
    assert [s.scope_id for s in await dhcp.list_scopes()] == ["scope-1"]


async def test_dhcp_apply_returns_changed_when_spec_differs() -> None:
    dhcp = FakeDhcp()
    base = DhcpScopeSpec(
        scope_id="scope-1",
        cidr="10.0.0.0/24",
        range_start="10.0.0.10",
        range_end="10.0.0.50",
    )
    enlarged = DhcpScopeSpec(
        scope_id="scope-1",
        cidr="10.0.0.0/24",
        range_start="10.0.0.10",
        range_end="10.0.0.99",
    )

    await dhcp.apply(base)
    assert await dhcp.apply(enlarged) is True


async def test_dhcp_delete_returns_true_only_when_present() -> None:
    dhcp = FakeDhcp()
    spec = DhcpScopeSpec(
        scope_id="scope-1",
        cidr="10.0.0.0/24",
        range_start="10.0.0.10",
        range_end="10.0.0.50",
    )
    await dhcp.apply(spec)

    assert await dhcp.delete("scope-1") is True
    assert await dhcp.delete("scope-1") is False


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------


async def test_dns_apply_and_resolve() -> None:
    dns = FakeDns()
    zone = DnsZoneSpec(
        zone="prod.lan",
        records=(DnsRecord(name="db", type="A", value="10.0.0.10"),),
    )

    assert await dns.apply(zone) is True
    assert await dns.apply(zone) is False
    assert await dns.resolve_check("prod.lan", "db") == "10.0.0.10"
    assert await dns.resolve_check("prod.lan", "missing") is None


# ---------------------------------------------------------------------------
# Firewall + NAT
# ---------------------------------------------------------------------------


async def test_firewall_policy_idempotency() -> None:
    fw = FakeFirewall()
    policy = FirewallPolicySpec(
        policy_id="policy-1",
        default_action=FirewallAction.DROP,
        rules=(
            FirewallRuleSpec(
                action=FirewallAction.ACCEPT,
                proto=FirewallProto.TCP,
                destination_port_start=22,
                destination_port_end=22,
            ),
        ),
    )

    assert await fw.apply_policy(policy) is True
    assert await fw.apply_policy(policy) is False
    assert [p.policy_id for p in await fw.list_policies()] == ["policy-1"]


async def test_nat_rule_idempotency() -> None:
    fw = FakeFirewall()
    rule = NatRuleSpec(
        rule_id="nat-1",
        source_cidr="10.10.0.0/24",
        egress_interface="eth0",
    )

    assert await fw.apply_nat(rule) is True
    assert await fw.apply_nat(rule) is False
    assert await fw.delete_nat("nat-1") is True
    assert await fw.delete_nat("nat-1") is False
