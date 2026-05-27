"""Компилятор политик безопасности → nftables ruleset (N2-02).

Принимает SecurityPolicy и генерирует nftables-скрипт, пригодный
для атомарной загрузки через ``nft -f``.

Архитектурные решения:
- Компилятор — чистая доменная служба (нет I/O, нет внешних зависимостей).
- Ссылки на SecurityGroup и AddressPool передаются через ``resolved_cidrs`` —
  словарь ``endpoint_key → list[str]``, заполняемый use case'ом перед вызовом.
- Если CIDR для ссылки не переданы, генерируется комментарий-заглушка,
  а правило пропускается (безопасное поведение).
- Каждое правило имеет счётчик ``counter`` — nftables будет инкрементировать
  его при совпадении (N2-04).

Формат ``resolved_cidrs``:
    {
        "security_group:sg_xxx": ["10.0.1.0/24", "10.0.2.0/24"],
        "address_pool:apool_yyy": ["192.168.0.0/16"],
    }
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdn_controller.core.entities.security_policy import SecurityPolicy, SecurityPolicyRule


def _rule_key(endpoint_type: str, endpoint_value: str) -> str:
    return f"{endpoint_type}:{endpoint_value}"


def _cidrs_for(
    ep_type: str,
    ep_value: str,
    resolved: dict[str, list[str]],
) -> list[str] | None:
    """Возвращает список CIDR для endpoint или None если тип 'any'."""
    if ep_type == "any":
        return None
    if ep_type == "cidr":
        return [ep_value] if ep_value else None
    return resolved.get(_rule_key(ep_type, ep_value))


def _nft_match(
    direction: str,
    src_cidrs: list[str] | None,
    dst_cidrs: list[str] | None,
    proto: str | None,
    dports: list[str] | None,
) -> list[str]:
    """Строит список nft-выражений совпадения для одного правила."""
    parts: list[str] = []

    # Версия IP (упрощённо: только IPv4 для MVP)
    if src_cidrs or dst_cidrs:
        parts.append("ip")

    if src_cidrs:
        if len(src_cidrs) == 1:
            parts.append(f"saddr {src_cidrs[0]}")
        else:
            parts.append("saddr { " + ", ".join(src_cidrs) + " }")

    if dst_cidrs:
        if len(dst_cidrs) == 1:
            parts.append(f"daddr {dst_cidrs[0]}")
        else:
            parts.append("daddr { " + ", ".join(dst_cidrs) + " }")

    if proto and proto != "any":
        parts.append(proto)

    if dports and proto in ("tcp", "udp"):
        if len(dports) == 1:
            parts.append(f"dport {dports[0]}")
        else:
            parts.append("dport { " + ", ".join(dports) + " }")

    return parts


class PolicyCompiler:
    """Компилятор SecurityPolicy → nftables-скрипт (N2-02).

    Не имеет состояния; вызывайте ``compile()`` для каждой политики.
    """

    def compile(
        self,
        policy: "SecurityPolicy",
        *,
        resolved_cidrs: dict[str, list[str]] | None = None,
        service_protos: dict[str, str] | None = None,
        service_ports: dict[str, list[str]] | None = None,
        now: datetime | None = None,
    ) -> str:
        """Компилирует политику в nftables-скрипт.

        Args:
            policy: исходная политика с правилами.
            resolved_cidrs: отображение endpoint_key → CIDRs. Если None —
                пустой словарь (все ссылочные правила станут комментариями).
            service_protos: отображение service_object_id → protocol.
            service_ports: отображение service_object_id → list[port_range].
            now: момент времени компиляции (по умолчанию — UTC now).

        Returns:
            Строка с nftables-скриптом, готовым для ``nft -f``.
        """
        resolved_cidrs = resolved_cidrs or {}
        service_protos = service_protos or {}
        service_ports = service_ports or {}
        if now is None:
            now = datetime.now(tz=timezone.utc)

        lines: list[str] = []
        lines.append("#!/usr/sbin/nft -f")
        lines.append(f"# SecurityPolicy: {policy.name} ({policy.id})")
        lines.append("")

        table_name = policy.id.replace("-", "_")
        lines.append(f"table inet {table_name} {{")

        # Формируем цепочки ingress и egress
        for direction_key, hook in (("ingress", "input"), ("egress", "output")):
            chain_rules = [
                r for r in policy.rules
                if r.enabled and r.direction in (direction_key, "both")
            ]
            lines.append(f"    chain {hook} {{")
            lines.append(f"        type filter hook {hook} priority 0; policy drop;")

            for rule in sorted(chain_rules, key=lambda r: r.priority):
                lines.extend(self._compile_rule(rule, resolved_cidrs, service_protos, service_ports))

            lines.append("    }")
            lines.append("")

        lines.append("}")
        return "\n".join(lines)

    def _compile_rule(
        self,
        rule: "SecurityPolicyRule",
        resolved: dict[str, list[str]],
        service_protos: dict[str, str],
        service_ports: dict[str, list[str]],
    ) -> list[str]:
        """Компилирует одно правило в список строк nftables."""
        src_cidrs = _cidrs_for(rule.source_type, rule.source_value, resolved)
        dst_cidrs = _cidrs_for(rule.destination_type, rule.destination_value, resolved)

        # Если ссылка есть, но CIDR не разрешены — пропускаем с комментарием
        if rule.source_type not in ("any", "cidr") and src_cidrs is None:
            return [
                f"        # ПРОПУЩЕНО rule_id={rule.rule_id}: "
                f"source {rule.source_type}:{rule.source_value} не разрешён"
            ]
        if rule.destination_type not in ("any", "cidr") and dst_cidrs is None:
            return [
                f"        # ПРОПУЩЕНО rule_id={rule.rule_id}: "
                f"destination {rule.destination_type}:{rule.destination_value} не разрешён"
            ]

        # Протокол и порты из ServiceObject
        proto: str | None = None
        dports: list[str] | None = None
        if rule.service_object_id:
            sobj_id = str(rule.service_object_id)
            proto = service_protos.get(sobj_id)
            dports = service_ports.get(sobj_id)

        match_parts = _nft_match(rule.direction, src_cidrs, dst_cidrs, proto, dports)
        match_str = " ".join(match_parts) if match_parts else ""

        verdict = "accept" if rule.action == "allow" else "drop"
        comment = f"  # {rule.comment}" if rule.comment else ""
        counter = "counter"

        stmt = f"        {match_str} {counter} {verdict}{comment}  # rule_id={rule.rule_id}"
        return [stmt]
