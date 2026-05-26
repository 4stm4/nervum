"""Use cases N2 — SecurityPolicy и TrunkPort.

N2-01  SecurityPolicy: CRUD, управление правилами
N2-02  PolicyCompiler: compile → nftables-скрипт
N2-03  apply/verify lifecycle
N2-04  Обновление счётчиков пакетов/байт
N2-05  TrunkPort: CRUD
N2-06  События outbox для всех мутирующих операций
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sdn_controller.core.entities.security_policy import SecurityPolicy, SecurityPolicyRule
from sdn_controller.core.entities.trunk_port import TrunkPort
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.services.policy_compiler import PolicyCompiler
from sdn_controller.core.value_objects.errors import NotFoundError, ValidationError
from sdn_controller.core.value_objects.ids import (
    IdFactory,
    LogicalPortId,
    NodeId,
    ProjectId,
    SecurityPolicyId,
    ServiceObjectId,
    TrunkPortId,
)
from sdn_controller.ports.persistence import (
    NodeRepository,
    SecurityPolicyRepository,
    ServiceObjectRepository,
    TrunkPortRepository,
)

__all__ = [
    # SecurityPolicy
    "CreateSecurityPolicyCommand",
    "UpdateSecurityPolicyCommand",
    "AddPolicyRuleCommand",
    "UpdateCountersCommand",
    "CreateSecurityPolicy",
    "GetSecurityPolicy",
    "ListSecurityPolicies",
    "UpdateSecurityPolicy",
    "DeleteSecurityPolicy",
    "AddPolicyRule",
    "RemovePolicyRule",
    "CompileSecurityPolicy",
    "ApplySecurityPolicy",
    "UpdateRuleCounters",
    # TrunkPort
    "CreateTrunkPortCommand",
    "UpdateTrunkPortCommand",
    "CreateTrunkPort",
    "GetTrunkPort",
    "ListTrunkPorts",
    "UpdateTrunkPort",
    "DeleteTrunkPort",
]

# ---------------------------------------------------------------------------
# SecurityPolicy use cases (N2-01, N2-03)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateSecurityPolicyCommand:
    name: str
    description: str = ""
    project_id: ProjectId | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class UpdateSecurityPolicyCommand:
    policy_id: SecurityPolicyId
    name: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class AddPolicyRuleCommand:
    """Команда добавления правила в политику."""

    policy_id: SecurityPolicyId
    priority: int
    direction: str                          # ingress | egress | both
    action: str                             # allow | deny
    source_type: str = "any"
    source_value: str = ""
    destination_type: str = "any"
    destination_value: str = ""
    service_object_id: ServiceObjectId | None = None
    enabled: bool = True
    comment: str = ""


@dataclass(frozen=True)
class UpdateCountersCommand:
    """Обновление счётчиков для одного правила (N2-04)."""

    policy_id: SecurityPolicyId
    rule_id: str
    packet_count: int
    byte_count: int


class CreateSecurityPolicy:
    """Создаёт новую политику безопасности в статусе draft."""

    def __init__(
        self,
        *,
        policies: SecurityPolicyRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateSecurityPolicyCommand) -> SecurityPolicy:
        now = self._clock.now()
        policy = SecurityPolicy(
            id=self._ids.security_policy(),
            name=cmd.name,
            description=cmd.description,
            project_id=cmd.project_id,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._policies.save(policy)
        await self._events.publish(
            event_type="security_policy.created",
            resource_type="security_policy",
            resource_id=policy.id,
            payload={"name": policy.name},
            project_id=policy.project_id,
        )
        return policy


class GetSecurityPolicy:
    def __init__(self, *, policies: SecurityPolicyRepository) -> None:
        self._policies = policies

    async def execute(self, policy_id: SecurityPolicyId) -> SecurityPolicy:
        policy = await self._policies.get(policy_id)
        if policy is None:
            raise NotFoundError(f"политика безопасности {policy_id} не найдена")
        return policy


class ListSecurityPolicies:
    def __init__(self, *, policies: SecurityPolicyRepository) -> None:
        self._policies = policies

    async def execute(
        self, *, project_id: ProjectId | None = None
    ) -> list[SecurityPolicy]:
        return await self._policies.list(project_id=project_id)


class UpdateSecurityPolicy:
    def __init__(
        self,
        *,
        policies: SecurityPolicyRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateSecurityPolicyCommand) -> SecurityPolicy:
        policy = await self._policies.get(cmd.policy_id)
        if policy is None:
            raise NotFoundError(f"политика безопасности {cmd.policy_id} не найдена")
        policy.update(
            name=cmd.name,
            description=cmd.description,
            labels=cmd.labels,
            now=self._clock.now(),
        )
        await self._policies.save(policy)
        await self._events.publish(
            event_type="security_policy.updated",
            resource_type="security_policy",
            resource_id=policy.id,
            project_id=policy.project_id,
        )
        return policy


class DeleteSecurityPolicy:
    def __init__(
        self,
        *,
        policies: SecurityPolicyRepository,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._events = events

    async def execute(self, policy_id: SecurityPolicyId) -> None:
        policy = await self._policies.get(policy_id)
        if policy is None:
            raise NotFoundError(f"политика безопасности {policy_id} не найдена")
        await self._policies.delete(policy_id)
        await self._events.publish(
            event_type="security_policy.deleted",
            resource_type="security_policy",
            resource_id=policy_id,
            project_id=policy.project_id,
        )


class AddPolicyRule:
    """Добавляет правило в политику; сбрасывает статус в draft (N2-01)."""

    def __init__(
        self,
        *,
        policies: SecurityPolicyRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._clock = clock
        self._events = events

    async def execute(self, cmd: AddPolicyRuleCommand) -> SecurityPolicy:
        policy = await self._policies.get(cmd.policy_id)
        if policy is None:
            raise NotFoundError(f"политика безопасности {cmd.policy_id} не найдена")
        rule = SecurityPolicyRule.new(
            priority=cmd.priority,
            direction=cmd.direction,
            action=cmd.action,
            source_type=cmd.source_type,
            source_value=cmd.source_value,
            destination_type=cmd.destination_type,
            destination_value=cmd.destination_value,
            service_object_id=cmd.service_object_id,
            enabled=cmd.enabled,
            comment=cmd.comment,
        )
        policy.add_rule(rule, now=self._clock.now())
        await self._policies.save(policy)
        await self._events.publish(
            event_type="security_policy.rule_added",
            resource_type="security_policy",
            resource_id=policy.id,
            payload={"rule_id": rule.rule_id, "priority": rule.priority},
            project_id=policy.project_id,
        )
        return policy


class RemovePolicyRule:
    """Удаляет правило из политики; сбрасывает статус в draft (N2-01)."""

    def __init__(
        self,
        *,
        policies: SecurityPolicyRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._clock = clock
        self._events = events

    async def execute(
        self, policy_id: SecurityPolicyId, rule_id: str
    ) -> SecurityPolicy:
        policy = await self._policies.get(policy_id)
        if policy is None:
            raise NotFoundError(f"политика безопасности {policy_id} не найдена")
        policy.remove_rule(rule_id, now=self._clock.now())
        await self._policies.save(policy)
        await self._events.publish(
            event_type="security_policy.rule_removed",
            resource_type="security_policy",
            resource_id=policy.id,
            payload={"rule_id": rule_id},
            project_id=policy.project_id,
        )
        return policy


class CompileSecurityPolicy:
    """Компилирует политику в nftables-скрипт; переводит в статус compiled (N2-02, N2-03)."""

    def __init__(
        self,
        *,
        policies: SecurityPolicyRepository,
        service_objects: ServiceObjectRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._service_objects = service_objects
        self._clock = clock
        self._events = events
        self._compiler = PolicyCompiler()

    async def execute(self, policy_id: SecurityPolicyId) -> SecurityPolicy:
        policy = await self._policies.get(policy_id)
        if policy is None:
            raise NotFoundError(f"политика безопасности {policy_id} не найдена")

        # Собираем разрешение ServiceObject → protocol + ports
        service_protos: dict[str, str] = {}
        service_ports: dict[str, list[str]] = {}
        seen_ids: set[str] = {
            str(r.service_object_id)
            for r in policy.rules
            if r.service_object_id is not None
        }
        for sobj_id_str in seen_ids:
            sobj = await self._service_objects.get(ServiceObjectId(sobj_id_str))
            if sobj is not None:
                service_protos[sobj_id_str] = sobj.protocol
                service_ports[sobj_id_str] = list(sobj.ports)

        now = self._clock.now()
        ruleset = self._compiler.compile(
            policy,
            resolved_cidrs={},  # будущее расширение: резолвинг SecurityGroup/AddressPool
            service_protos=service_protos,
            service_ports=service_ports,
            now=now,
        )
        policy.mark_compiled(ruleset=ruleset, now=now)
        await self._policies.save(policy)
        await self._events.publish(
            event_type="security_policy.compiled",
            resource_type="security_policy",
            resource_id=policy.id,
            project_id=policy.project_id,
        )
        return policy


class ApplySecurityPolicy:
    """Применяет скомпилированную политику; переводит в статус applied (N2-03).

    В текущей реализации — логический переход без реального вызова агента.
    Полноценная интеграция с агентом — N3+.
    """

    def __init__(
        self,
        *,
        policies: SecurityPolicyRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._clock = clock
        self._events = events

    async def execute(self, policy_id: SecurityPolicyId) -> SecurityPolicy:
        policy = await self._policies.get(policy_id)
        if policy is None:
            raise NotFoundError(f"политика безопасности {policy_id} не найдена")
        policy.mark_applied(now=self._clock.now())
        await self._policies.save(policy)
        await self._events.publish(
            event_type="security_policy.applied",
            resource_type="security_policy",
            resource_id=policy.id,
            project_id=policy.project_id,
        )
        return policy


class UpdateRuleCounters:
    """Обновляет счётчики пакетов/байт для конкретного правила (N2-04)."""

    def __init__(
        self,
        *,
        policies: SecurityPolicyRepository,
        clock: Clock,
    ) -> None:
        self._policies = policies
        self._clock = clock

    async def execute(self, cmd: UpdateCountersCommand) -> SecurityPolicy:
        policy = await self._policies.get(cmd.policy_id)
        if policy is None:
            raise NotFoundError(f"политика безопасности {cmd.policy_id} не найдена")
        policy.update_counters(
            cmd.rule_id,
            packet_count=cmd.packet_count,
            byte_count=cmd.byte_count,
        )
        await self._policies.save(policy)
        return policy


# ---------------------------------------------------------------------------
# TrunkPort use cases (N2-05)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateTrunkPortCommand:
    name: str
    node_id: NodeId
    vlan_ids: list[int]
    logical_port_id: LogicalPortId | None = None
    native_vlan: int | None = None
    project_id: ProjectId | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class UpdateTrunkPortCommand:
    port_id: TrunkPortId
    name: str | None = None
    vlan_ids: list[int] | None = None
    native_vlan: int | None = None
    labels: dict[str, str] | None = None


class CreateTrunkPort:
    """Создаёт транковый порт 802.1q на узле (N2-05)."""

    def __init__(
        self,
        *,
        trunks: TrunkPortRepository,
        nodes: NodeRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._trunks = trunks
        self._nodes = nodes
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateTrunkPortCommand) -> TrunkPort:
        if await self._nodes.get(cmd.node_id) is None:
            raise NotFoundError(f"узел {cmd.node_id} не найден")
        now = self._clock.now()
        port = TrunkPort(
            id=self._ids.trunk_port(),
            name=cmd.name,
            node_id=cmd.node_id,
            logical_port_id=cmd.logical_port_id,
            vlan_ids=tuple(cmd.vlan_ids),
            native_vlan=cmd.native_vlan,
            project_id=cmd.project_id,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._trunks.save(port)
        await self._events.publish(
            event_type="trunk_port.created",
            resource_type="trunk_port",
            resource_id=port.id,
            payload={"name": port.name, "node_id": port.node_id, "vlan_ids": list(port.vlan_ids)},
            project_id=port.project_id,
        )
        return port


class GetTrunkPort:
    def __init__(self, *, trunks: TrunkPortRepository) -> None:
        self._trunks = trunks

    async def execute(self, port_id: TrunkPortId) -> TrunkPort:
        port = await self._trunks.get(port_id)
        if port is None:
            raise NotFoundError(f"транковый порт {port_id} не найден")
        return port


class ListTrunkPorts:
    def __init__(self, *, trunks: TrunkPortRepository) -> None:
        self._trunks = trunks

    async def execute(
        self,
        *,
        node_id: NodeId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[TrunkPort]:
        return await self._trunks.list(node_id=node_id, project_id=project_id)


class UpdateTrunkPort:
    def __init__(
        self,
        *,
        trunks: TrunkPortRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._trunks = trunks
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateTrunkPortCommand) -> TrunkPort:
        port = await self._trunks.get(cmd.port_id)
        if port is None:
            raise NotFoundError(f"транковый порт {cmd.port_id} не найден")
        port.update(
            name=cmd.name,
            vlan_ids=tuple(cmd.vlan_ids) if cmd.vlan_ids is not None else None,
            native_vlan=cmd.native_vlan,
            labels=cmd.labels,
            now=self._clock.now(),
        )
        await self._trunks.save(port)
        await self._events.publish(
            event_type="trunk_port.updated",
            resource_type="trunk_port",
            resource_id=port.id,
            project_id=port.project_id,
        )
        return port


class DeleteTrunkPort:
    def __init__(
        self,
        *,
        trunks: TrunkPortRepository,
        events: EventPublisher,
    ) -> None:
        self._trunks = trunks
        self._events = events

    async def execute(self, port_id: TrunkPortId) -> None:
        port = await self._trunks.get(port_id)
        if port is None:
            raise NotFoundError(f"транковый порт {port_id} не найден")
        await self._trunks.delete(port_id)
        await self._events.publish(
            event_type="trunk_port.deleted",
            resource_type="trunk_port",
            resource_id=port_id,
            project_id=port.project_id,
        )
