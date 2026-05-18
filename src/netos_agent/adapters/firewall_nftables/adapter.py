"""Production firewall + NAT via ``nftables``.

We own a single inet table ``sdn_controller`` with two scoped chains:

* ``filter`` (input + forward) — carries the per-network policies as
  separate sub-chains ``policy_<policy_id>``;
* ``nat`` (postrouting) — carries our masquerade rules.

Apply path is transactional:

1. Generate a complete ``nft`` script for the *whole* sdn-owned table
   (existing entries we want to keep + the new addition).
2. Run ``nft --check -f <tmp>`` to confirm the script is syntactically
   valid against the running kernel.
3. Run ``nft -f <tmp>`` — that's an atomic kernel-level swap.

Reads use ``nft -j list table inet sdn_controller`` and parse JSON output.

We deliberately keep *our* table separate from the host's main table so
operators retain control of their base firewall — we only sweep what we
own.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from pathlib import Path
from typing import Any

from netos_agent.core.value_objects.edge_services import (
    FirewallPolicySpec,
    FirewallProto,
    FirewallRuleSpec,
    NatRuleSpec,
)
from netos_agent.core.value_objects.errors import OvsdbError
from netos_agent.ports.firewall import FirewallCounters

_TABLE = "sdn_controller"
_FILTER_FAMILY = "inet"
_NAT_FAMILY = "ip"  # nftables IPv4 NAT; IPv6 NAT lives in ``ip6`` if needed later
_DEFAULT_TIMEOUT_S = 10.0


class NftablesFirewall:
    def __init__(
        self,
        *,
        nft: str = "nft",
        scratch_dir: Path | str = "/run/sdn-controller/nft",
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._nft = nft
        self._scratch_dir = Path(scratch_dir)
        self._timeout = timeout
        # State held purely so we can re-render the script. Reflects the
        # *intent* we last successfully applied — not the kernel state.
        self._policies: dict[str, FirewallPolicySpec] = {}
        self._nat: dict[str, NatRuleSpec] = {}
        self._lock = asyncio.Lock()

    # -- FirewallPort: policies -------------------------------------------

    async def validate_policy(self, policy: FirewallPolicySpec) -> None:
        await self._check_script(self._render(extra_policy=policy))

    async def apply_policy(self, policy: FirewallPolicySpec) -> bool:
        async with self._lock:
            existing = self._policies.get(policy.policy_id)
            if existing == policy:
                return False
            self._policies[policy.policy_id] = policy
            await self._apply_script()
            return True

    async def delete_policy(self, policy_id: str) -> bool:
        async with self._lock:
            if policy_id not in self._policies:
                return False
            del self._policies[policy_id]
            await self._apply_script()
            return True

    async def list_policies(self) -> list[FirewallPolicySpec]:
        async with self._lock:
            return list(self._policies.values())

    # -- FirewallPort: NAT -------------------------------------------------

    async def validate_nat(self, rule: NatRuleSpec) -> None:
        await self._check_script(self._render(extra_nat=rule))

    async def apply_nat(self, rule: NatRuleSpec) -> bool:
        async with self._lock:
            existing = self._nat.get(rule.rule_id)
            if existing == rule:
                return False
            self._nat[rule.rule_id] = rule
            await self._apply_script()
            return True

    async def delete_nat(self, rule_id: str) -> bool:
        async with self._lock:
            if rule_id not in self._nat:
                return False
            del self._nat[rule_id]
            await self._apply_script()
            return True

    async def list_nat_rules(self) -> list[NatRuleSpec]:
        async with self._lock:
            return list(self._nat.values())

    # -- FirewallPort: counters -------------------------------------------

    async def get_counters(self, policy_id: str) -> FirewallCounters:
        try:
            raw = await self._run(self._nft, "-j", "list", "table", _FILTER_FAMILY, _TABLE)
        except OvsdbError:
            return FirewallCounters(policy_id=policy_id)
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            return FirewallCounters(policy_id=policy_id)
        chain = _policy_chain_name(policy_id)
        packets = 0
        bytes_ = 0
        for item in doc.get("nftables", []):
            rule = item.get("rule")
            if not isinstance(rule, dict) or rule.get("chain") != chain:
                continue
            for expr in rule.get("expr", []):
                counter = expr.get("counter") if isinstance(expr, dict) else None
                if isinstance(counter, dict):
                    packets += int(counter.get("packets", 0))
                    bytes_ += int(counter.get("bytes", 0))
        return FirewallCounters(policy_id=policy_id, packets=packets, bytes=bytes_)

    # -- internals ---------------------------------------------------------

    async def _apply_script(self) -> None:
        script = self._render()
        await self._check_script(script)
        path = await asyncio.to_thread(self._scratch_path)
        await asyncio.to_thread(path.write_text, script, "utf-8")
        await self._run(self._nft, "-f", str(path))

    def _scratch_path(self) -> Path:
        self._scratch_dir.mkdir(parents=True, exist_ok=True)
        return self._scratch_dir / "sdn.nft"

    async def _check_script(self, script: str) -> None:
        if shutil.which(self._nft) is None:
            raise OvsdbError(f"{self._nft!r} not found on PATH")
        tmp = await asyncio.to_thread(self._scratch_path)
        scratch = tmp.with_suffix(".check.nft")
        await asyncio.to_thread(scratch.write_text, script, "utf-8")
        try:
            await self._run(self._nft, "--check", "-f", str(scratch))
        finally:
            await asyncio.to_thread(_unlink_quiet, scratch)

    def _render(
        self,
        *,
        extra_policy: FirewallPolicySpec | None = None,
        extra_nat: NatRuleSpec | None = None,
    ) -> str:
        policies = dict(self._policies)
        if extra_policy is not None:
            policies[extra_policy.policy_id] = extra_policy
        nat = dict(self._nat)
        if extra_nat is not None:
            nat[extra_nat.rule_id] = extra_nat

        lines = [
            "#!/usr/sbin/nft -f",
            "# managed by sdn-controller",
            f"flush table {_FILTER_FAMILY} {_TABLE}",
            f"flush table {_NAT_FAMILY} {_TABLE}_nat",
        ]
        # Filter table — input + forward share a default action; per-policy
        # chains hook off them so we can sweep per-tenant rules cleanly.
        lines.append(f"table {_FILTER_FAMILY} {_TABLE} {{")
        for policy_id, policy in policies.items():
            chain = _policy_chain_name(policy_id)
            lines.append(f"    chain {chain} {{")
            lines.append("        counter")
            for rule in policy.rules:
                lines.append("        " + _render_rule(rule))
            lines.append(f"        {policy.default_action.value}")
            lines.append("    }")
        # Single dispatch chain that calls every policy chain in order.
        # In a richer model we'd select by ingress iface; M7 ships the
        # simple all-policies-active variant.
        lines.append("    chain dispatch {")
        lines.append("        type filter hook forward priority 0; policy accept;")
        for policy_id in policies:
            lines.append(f"        jump {_policy_chain_name(policy_id)}")
        lines.append("    }")
        lines.append("}")

        # NAT table — postrouting masquerade per rule.
        lines.append(f"table {_NAT_FAMILY} {_TABLE}_nat {{")
        lines.append("    chain postrouting {")
        lines.append("        type nat hook postrouting priority srcnat; policy accept;")
        for nat_rule in nat.values():
            lines.append(
                f'        ip saddr {nat_rule.source_cidr} oifname "{nat_rule.egress_interface}" '
                f'counter masquerade comment "{nat_rule.rule_id}"'
            )
        lines.append("    }")
        lines.append("}")
        return "\n".join(lines) + "\n"

    async def _run(self, *args: str) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise OvsdbError(f"{args[0]!r} not found on PATH") from exc
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise OvsdbError(f"command timed out: {' '.join(args)}") from exc

        if proc.returncode != 0:
            raise OvsdbError(
                f"command failed ({proc.returncode}): {' '.join(args)}: "
                f"{stderr_b.decode(errors='replace').strip()}"
            )
        return stdout_b.decode()


# ---------------------------------------------------------------------------
# Render helpers (pure)
# ---------------------------------------------------------------------------


def _policy_chain_name(policy_id: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in policy_id)
    return f"policy_{safe}"


def _render_rule(rule: FirewallRuleSpec) -> str:
    matches: list[str] = []
    if rule.source_cidr is not None:
        matches.append(f"ip saddr {rule.source_cidr}")
    if rule.destination_cidr is not None:
        matches.append(f"ip daddr {rule.destination_cidr}")
    if rule.proto != FirewallProto.ANY:
        matches.append(f"meta l4proto {rule.proto.value}")
    if rule.destination_port_start is not None:
        end = rule.destination_port_end or rule.destination_port_start
        proto = rule.proto.value if rule.proto in {FirewallProto.TCP, FirewallProto.UDP} else "tcp"
        matches.append(f"{proto} dport {{ {rule.destination_port_start}-{end} }}")
    matches.append("counter")
    matches.append(rule.action.value)
    return " ".join(matches)


def _unlink_quiet(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _Any() -> Any:
    return None
