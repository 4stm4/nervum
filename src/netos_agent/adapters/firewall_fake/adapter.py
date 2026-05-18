"""``FirewallPort`` that stores rules in process memory.

Counters are returned as fixed-zero so callers can wire their own
fixtures via ``seed_counters``.
"""

from __future__ import annotations

import anyio

from netos_agent.core.value_objects.edge_services import (
    FirewallPolicySpec,
    NatRuleSpec,
)
from netos_agent.ports.firewall import FirewallCounters


class FakeFirewall:
    def __init__(self) -> None:
        self._policies: dict[str, FirewallPolicySpec] = {}
        self._nat: dict[str, NatRuleSpec] = {}
        self._counters: dict[str, FirewallCounters] = {}
        self._lock = anyio.Lock()

    # -- policy ------------------------------------------------------------

    async def validate_policy(self, policy: FirewallPolicySpec) -> None:
        return None

    async def apply_policy(self, policy: FirewallPolicySpec) -> bool:
        async with self._lock:
            existing = self._policies.get(policy.policy_id)
            if existing == policy:
                return False
            self._policies[policy.policy_id] = policy
            return True

    async def delete_policy(self, policy_id: str) -> bool:
        async with self._lock:
            return self._policies.pop(policy_id, None) is not None

    async def list_policies(self) -> list[FirewallPolicySpec]:
        async with self._lock:
            return list(self._policies.values())

    # -- nat ---------------------------------------------------------------

    async def validate_nat(self, rule: NatRuleSpec) -> None:
        return None

    async def apply_nat(self, rule: NatRuleSpec) -> bool:
        async with self._lock:
            existing = self._nat.get(rule.rule_id)
            if existing == rule:
                return False
            self._nat[rule.rule_id] = rule
            return True

    async def delete_nat(self, rule_id: str) -> bool:
        async with self._lock:
            return self._nat.pop(rule_id, None) is not None

    async def list_nat_rules(self) -> list[NatRuleSpec]:
        async with self._lock:
            return list(self._nat.values())

    # -- counters ----------------------------------------------------------

    async def get_counters(self, policy_id: str) -> FirewallCounters:
        async with self._lock:
            return self._counters.get(policy_id, FirewallCounters(policy_id=policy_id))

    async def seed_counters(self, counters: FirewallCounters) -> None:
        async with self._lock:
            self._counters[counters.policy_id] = counters
