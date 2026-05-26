"""Сущность SecurityPolicy — упорядоченный набор правил фильтрации (N2-01).

Каждое правило описывает условие совпадения (source/destination/service)
и действие (allow/deny). Правила упорядочены по приоритету: меньший номер
означает более высокий приоритет. Политика проходит lifecycle:
draft → compiled → applied (N2-03).

Компилятор (PolicyCompiler) преобразует правила в nftables-скрипт (N2-02).
Счётчики пакетов и байт обновляются при верификации агента (N2-04).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_network

from sdn_controller.core.value_objects.enums import SecurityPolicyStatus
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import (
    ProjectId,
    SecurityPolicyId,
    ServiceObjectId,
)

# Допустимые типы источника/назначения в правиле
_ENDPOINT_TYPES = frozenset({"security_group", "address_pool", "cidr", "any"})
# Допустимые направления правила
_DIRECTIONS = frozenset({"ingress", "egress", "both"})
# Допустимые действия правила
_ACTIONS = frozenset({"allow", "deny"})


@dataclass
class SecurityPolicyRule:
    """Одно правило в политике безопасности.

    Поля source_type/source_value и destination_type/destination_value
    описывают endpoint-у по схеме ``(тип, значение)``:
    - ``("security_group", "sg_xxx")``  — ссылка на SecurityGroup
    - ``("address_pool", "apool_xxx")`` — ссылка на AddressPool
    - ``("cidr", "10.0.0.0/8")``        — явный CIDR
    - ``("any", "")``                   — любой адрес
    """

    rule_id: str
    priority: int                          # 1–65535, меньше = выше приоритет
    direction: str                         # ingress | egress | both
    action: str                            # allow | deny
    source_type: str = "any"
    source_value: str = ""
    destination_type: str = "any"
    destination_value: str = ""
    service_object_id: ServiceObjectId | None = None
    enabled: bool = True
    comment: str = ""
    # Счётчики (N2-04), обновляются верификатором
    packet_count: int = 0
    byte_count: int = 0

    def __post_init__(self) -> None:
        if not (1 <= self.priority <= 65535):
            raise ValidationError(f"priority должен быть в диапазоне 1–65535, получено {self.priority}")
        if self.direction not in _DIRECTIONS:
            raise ValidationError(f"direction должен быть одним из {_DIRECTIONS}, получено {self.direction!r}")
        if self.action not in _ACTIONS:
            raise ValidationError(f"action должен быть одним из {_ACTIONS}, получено {self.action!r}")
        if self.source_type not in _ENDPOINT_TYPES:
            raise ValidationError(f"source_type: недопустимое значение {self.source_type!r}")
        if self.destination_type not in _ENDPOINT_TYPES:
            raise ValidationError(f"destination_type: недопустимое значение {self.destination_type!r}")
        # Проверяем CIDR-значения
        for ep_type, ep_val in (
            (self.source_type, self.source_value),
            (self.destination_type, self.destination_value),
        ):
            if ep_type == "cidr" and ep_val:
                try:
                    ip_network(ep_val, strict=False)
                except ValueError:
                    raise ValidationError(f"некорректный CIDR {ep_val!r}")

    @staticmethod
    def new(
        *,
        priority: int,
        direction: str,
        action: str,
        source_type: str = "any",
        source_value: str = "",
        destination_type: str = "any",
        destination_value: str = "",
        service_object_id: ServiceObjectId | None = None,
        enabled: bool = True,
        comment: str = "",
    ) -> "SecurityPolicyRule":
        """Создаёт правило с автоматически генерируемым ``rule_id``."""
        return SecurityPolicyRule(
            rule_id=uuid.uuid4().hex[:12],
            priority=priority,
            direction=direction,
            action=action,
            source_type=source_type,
            source_value=source_value,
            destination_type=destination_type,
            destination_value=destination_value,
            service_object_id=service_object_id,
            enabled=enabled,
            comment=comment,
        )


@dataclass
class SecurityPolicy:
    """Политика безопасности — упорядоченный набор правил (N2-01).

    Правила хранятся в tuple, отсортированном по priority ASC. Изменение
    набора правил переводит политику обратно в статус ``draft``.
    """

    id: SecurityPolicyId
    name: str
    description: str = ""
    project_id: ProjectId | None = None
    labels: dict[str, str] = field(default_factory=dict)
    rules: tuple[SecurityPolicyRule, ...] = field(default_factory=tuple)
    status: SecurityPolicyStatus = SecurityPolicyStatus.DRAFT
    # Скомпилированный nftables-скрипт (N2-02), None до первой компиляции
    compiled_ruleset: str | None = None
    compiled_at: datetime | None = None
    applied_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now())
    updated_at: datetime = field(default_factory=lambda: datetime.now())

    def add_rule(self, rule: SecurityPolicyRule, *, now: datetime) -> None:
        """Добавляет правило; результирующий список сортируется по priority."""
        new_rules = (*self.rules, rule)
        self.rules = tuple(sorted(new_rules, key=lambda r: r.priority))
        self.status = SecurityPolicyStatus.DRAFT
        self.compiled_ruleset = None
        self.updated_at = now

    def remove_rule(self, rule_id: str, *, now: datetime) -> None:
        """Удаляет правило по ``rule_id``; сбрасывает компиляцию."""
        new_rules = tuple(r for r in self.rules if r.rule_id != rule_id)
        if len(new_rules) == len(self.rules):
            raise ValidationError(f"правило {rule_id!r} не найдено в политике")
        self.rules = new_rules
        self.status = SecurityPolicyStatus.DRAFT
        self.compiled_ruleset = None
        self.updated_at = now

    def mark_compiled(self, *, ruleset: str, now: datetime) -> None:
        """Сохраняет скомпилированный ruleset и переводит в статус compiled."""
        self.compiled_ruleset = ruleset
        self.compiled_at = now
        self.status = SecurityPolicyStatus.COMPILED
        self.updated_at = now

    def mark_applied(self, *, now: datetime) -> None:
        """Помечает политику как применённую на узлах."""
        if self.status != SecurityPolicyStatus.COMPILED:
            raise ValidationError(
                "политику можно применить только в статусе compiled; "
                f"текущий статус: {self.status}"
            )
        self.applied_at = now
        self.status = SecurityPolicyStatus.APPLIED
        self.updated_at = now

    def mark_failed(self, *, now: datetime) -> None:
        """Переводит политику в статус failed после неудачного применения."""
        self.status = SecurityPolicyStatus.FAILED
        self.updated_at = now

    def update_counters(
        self,
        rule_id: str,
        *,
        packet_count: int,
        byte_count: int,
    ) -> None:
        """Обновляет счётчики пакетов/байт для конкретного правила (N2-04)."""
        for rule in self.rules:
            if rule.rule_id == rule_id:
                rule.packet_count = packet_count
                rule.byte_count = byte_count
                return
        raise ValidationError(f"правило {rule_id!r} не найдено в политике")

    def update(
        self,
        *,
        name: str | None,
        description: str | None,
        labels: dict[str, str] | None,
        now: datetime,
    ) -> None:
        """Обновляет метаданные политики; сбрасывает компиляцию если изменилось."""
        changed = False
        if name is not None and name != self.name:
            self.name = name
            changed = True
        if description is not None and description != self.description:
            self.description = description
            changed = True
        if labels is not None:
            self.labels = labels
            changed = True
        if changed:
            self.updated_at = now
