"""Firewall port — covers NAT + policy.

NAT and policy share a backend (nftables in production) and a transactional
model: both are applied together via ``nft -f`` so the host never sits in a
half-configured state. The port exposes them separately so the dispatcher
can target one without the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from netos_agent.core.value_objects.edge_services import (
    FirewallPolicySpec,
    NatRuleSpec,
)


@dataclass(frozen=True, slots=True)
class FirewallCounters:
    """Best-effort counters for one policy + its NAT rules."""

    policy_id: str
    packets: int = 0
    bytes: int = 0


class FirewallPort(Protocol):
    async def validate_policy(self, policy: FirewallPolicySpec) -> None: ...
    async def apply_policy(self, policy: FirewallPolicySpec) -> bool: ...
    async def delete_policy(self, policy_id: str) -> bool: ...
    async def list_policies(self) -> list[FirewallPolicySpec]: ...

    async def validate_nat(self, rule: NatRuleSpec) -> None: ...
    async def apply_nat(self, rule: NatRuleSpec) -> bool: ...
    async def delete_nat(self, rule_id: str) -> bool: ...
    async def list_nat_rules(self) -> list[NatRuleSpec]: ...

    async def get_counters(self, policy_id: str) -> FirewallCounters: ...
