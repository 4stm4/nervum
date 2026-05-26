"""N2 REST-роутеры — SecurityPolicy и TrunkPort.

Маршруты:
  /security-policies                   — CRUD политик безопасности
  /security-policies/{id}/rules        — управление правилами
  /security-policies/{id}/compile      — компиляция в nftables (N2-02)
  /security-policies/{id}/apply        — применение скомпилированной политики (N2-03)
  /trunk-ports                         — CRUD trunk-портов 802.1q (N2-05)

Права:
  NETWORK_READ  — список / детали
  NETWORK_WRITE — создание / изменение / компиляция / применение / удаление
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from sdn_controller.adapters.http_api.auth import require as require_permission
from sdn_controller.app.container import Container
from sdn_controller.core.use_cases.n2 import (
    AddPolicyRule,
    AddPolicyRuleCommand,
    ApplySecurityPolicy,
    CompileSecurityPolicy,
    CreateSecurityPolicy,
    CreateSecurityPolicyCommand,
    CreateTrunkPort,
    CreateTrunkPortCommand,
    DeleteSecurityPolicy,
    DeleteTrunkPort,
    GetSecurityPolicy,
    GetTrunkPort,
    ListSecurityPolicies,
    ListTrunkPorts,
    RemovePolicyRule,
    UpdateSecurityPolicy,
    UpdateSecurityPolicyCommand,
    UpdateTrunkPort,
    UpdateTrunkPortCommand,
)
from sdn_controller.core.value_objects.ids import (
    NodeId,
    ProjectId,
    SecurityPolicyId,
    ServiceObjectId,
    TrunkPortId,
)
from sdn_controller.core.value_objects.security import Permission


def _container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Pydantic-схемы — SecurityPolicy
# ---------------------------------------------------------------------------


class SecurityPolicyCreateRequest(BaseModel):
    name: str
    description: str = ""
    project_id: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class SecurityPolicyUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


class PolicyRuleAddRequest(BaseModel):
    priority: int
    direction: str           # ingress | egress | both
    action: str              # allow | deny
    source_type: str = "any"
    source_value: str = ""
    destination_type: str = "any"
    destination_value: str = ""
    service_object_id: str | None = None
    enabled: bool = True
    comment: str = ""


class PolicyRuleOut(BaseModel):
    rule_id: str
    priority: int
    direction: str
    action: str
    source_type: str
    source_value: str
    destination_type: str
    destination_value: str
    service_object_id: str | None
    enabled: bool
    comment: str
    packet_count: int
    byte_count: int


class SecurityPolicyOut(BaseModel):
    id: str
    name: str
    description: str
    project_id: str | None
    labels: dict[str, str]
    status: str
    compiled_ruleset: str | None
    compiled_at: str | None
    applied_at: str | None
    rules: list[PolicyRuleOut]
    created_at: str
    updated_at: str


class SecurityPolicyListResponse(BaseModel):
    items: list[SecurityPolicyOut]


# ---------------------------------------------------------------------------
# Pydantic-схемы — TrunkPort
# ---------------------------------------------------------------------------


class TrunkPortCreateRequest(BaseModel):
    name: str
    node_id: str
    vlan_ids: list[int] = Field(default_factory=list)
    logical_port_id: str | None = None
    native_vlan: int | None = None
    project_id: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class TrunkPortUpdateRequest(BaseModel):
    name: str | None = None
    vlan_ids: list[int] | None = None
    native_vlan: int | None = None
    labels: dict[str, str] | None = None


class TrunkPortOut(BaseModel):
    id: str
    name: str
    node_id: str
    logical_port_id: str | None
    vlan_ids: list[int]
    native_vlan: int | None
    project_id: str | None
    labels: dict[str, str]
    created_at: str
    updated_at: str


class TrunkPortListResponse(BaseModel):
    items: list[TrunkPortOut]


# ---------------------------------------------------------------------------
# Вспомогательные сериализаторы
# ---------------------------------------------------------------------------


def _rule_out(rule: Any) -> PolicyRuleOut:
    return PolicyRuleOut(
        rule_id=rule.rule_id,
        priority=rule.priority,
        direction=rule.direction,
        action=rule.action,
        source_type=rule.source_type,
        source_value=rule.source_value,
        destination_type=rule.destination_type,
        destination_value=rule.destination_value,
        service_object_id=str(rule.service_object_id) if rule.service_object_id else None,
        enabled=rule.enabled,
        comment=rule.comment,
        packet_count=rule.packet_count,
        byte_count=rule.byte_count,
    )


def _policy_out(policy: Any) -> SecurityPolicyOut:
    return SecurityPolicyOut(
        id=policy.id,
        name=policy.name,
        description=policy.description,
        project_id=policy.project_id,
        labels=policy.labels,
        status=policy.status,
        compiled_ruleset=policy.compiled_ruleset,
        compiled_at=policy.compiled_at.isoformat() if policy.compiled_at else None,
        applied_at=policy.applied_at.isoformat() if policy.applied_at else None,
        rules=[_rule_out(r) for r in policy.rules],
        created_at=policy.created_at.isoformat(),
        updated_at=policy.updated_at.isoformat(),
    )


def _trunk_out(port: Any) -> TrunkPortOut:
    return TrunkPortOut(
        id=port.id,
        name=port.name,
        node_id=port.node_id,
        logical_port_id=port.logical_port_id,
        vlan_ids=list(port.vlan_ids),
        native_vlan=port.native_vlan,
        project_id=port.project_id,
        labels=port.labels,
        created_at=port.created_at.isoformat(),
        updated_at=port.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# SecurityPolicy router
# ---------------------------------------------------------------------------

security_policies_router = APIRouter(tags=["security-policies"])


@security_policies_router.post(
    "/security-policies",
    status_code=status.HTTP_201_CREATED,
    summary="Создать политику безопасности",
)
async def create_security_policy(
    body: SecurityPolicyCreateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: CreateSecurityPolicy = _container(request).create_security_policy
    policy = await uc.execute(
        CreateSecurityPolicyCommand(
            name=body.name,
            description=body.description,
            project_id=ProjectId(body.project_id) if body.project_id else None,
            labels=body.labels,
        )
    )
    return {"security_policy": _policy_out(policy).model_dump()}


@security_policies_router.get(
    "/security-policies",
    summary="Список политик безопасности",
)
async def list_security_policies(
    request: Request,
    project_id: str | None = None,
    _auth: Any = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    uc: ListSecurityPolicies = _container(request).list_security_policies
    policies = await uc.execute(
        project_id=ProjectId(project_id) if project_id else None,
    )
    return {"items": [_policy_out(p).model_dump() for p in policies]}


@security_policies_router.get(
    "/security-policies/{policy_id}",
    summary="Получить политику безопасности",
)
async def get_security_policy(
    policy_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    uc: GetSecurityPolicy = _container(request).get_security_policy
    policy = await uc.execute(SecurityPolicyId(policy_id))
    return {"security_policy": _policy_out(policy).model_dump()}


@security_policies_router.patch(
    "/security-policies/{policy_id}",
    summary="Обновить политику безопасности",
)
async def update_security_policy(
    policy_id: str,
    body: SecurityPolicyUpdateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: UpdateSecurityPolicy = _container(request).update_security_policy
    policy = await uc.execute(
        UpdateSecurityPolicyCommand(
            policy_id=SecurityPolicyId(policy_id),
            name=body.name,
            description=body.description,
            labels=body.labels,
        )
    )
    return {"security_policy": _policy_out(policy).model_dump()}


@security_policies_router.delete(
    "/security-policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить политику безопасности",
)
async def delete_security_policy(
    policy_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    uc: DeleteSecurityPolicy = _container(request).delete_security_policy
    await uc.execute(SecurityPolicyId(policy_id))


# Правила (sub-resource)


@security_policies_router.post(
    "/security-policies/{policy_id}/rules",
    status_code=status.HTTP_201_CREATED,
    summary="Добавить правило в политику",
)
async def add_policy_rule(
    policy_id: str,
    body: PolicyRuleAddRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: AddPolicyRule = _container(request).add_policy_rule
    policy = await uc.execute(
        AddPolicyRuleCommand(
            policy_id=SecurityPolicyId(policy_id),
            priority=body.priority,
            direction=body.direction,
            action=body.action,
            source_type=body.source_type,
            source_value=body.source_value,
            destination_type=body.destination_type,
            destination_value=body.destination_value,
            service_object_id=ServiceObjectId(body.service_object_id)
            if body.service_object_id
            else None,
            enabled=body.enabled,
            comment=body.comment,
        )
    )
    return {"security_policy": _policy_out(policy).model_dump()}


@security_policies_router.delete(
    "/security-policies/{policy_id}/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить правило из политики",
)
async def remove_policy_rule(
    policy_id: str,
    rule_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    uc: RemovePolicyRule = _container(request).remove_policy_rule
    await uc.execute(SecurityPolicyId(policy_id), rule_id)


# Lifecycle actions


@security_policies_router.post(
    "/security-policies/{policy_id}/compile",
    summary="Скомпилировать политику в nftables-скрипт (N2-02)",
)
async def compile_security_policy(
    policy_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: CompileSecurityPolicy = _container(request).compile_security_policy
    policy = await uc.execute(SecurityPolicyId(policy_id))
    return {"security_policy": _policy_out(policy).model_dump()}


@security_policies_router.post(
    "/security-policies/{policy_id}/apply",
    summary="Применить скомпилированную политику (N2-03)",
)
async def apply_security_policy(
    policy_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: ApplySecurityPolicy = _container(request).apply_security_policy
    policy = await uc.execute(SecurityPolicyId(policy_id))
    return {"security_policy": _policy_out(policy).model_dump()}


# ---------------------------------------------------------------------------
# TrunkPort router
# ---------------------------------------------------------------------------

trunk_ports_router = APIRouter(tags=["trunk-ports"])


@trunk_ports_router.post(
    "/trunk-ports",
    status_code=status.HTTP_201_CREATED,
    summary="Создать trunk-порт 802.1q (N2-05)",
)
async def create_trunk_port(
    body: TrunkPortCreateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: CreateTrunkPort = _container(request).create_trunk_port
    port = await uc.execute(
        CreateTrunkPortCommand(
            name=body.name,
            node_id=NodeId(body.node_id),
            vlan_ids=body.vlan_ids,
            logical_port_id=body.logical_port_id,  # type: ignore[arg-type]
            native_vlan=body.native_vlan,
            project_id=ProjectId(body.project_id) if body.project_id else None,
            labels=body.labels,
        )
    )
    return {"trunk_port": _trunk_out(port).model_dump()}


@trunk_ports_router.get(
    "/trunk-ports",
    summary="Список trunk-портов",
)
async def list_trunk_ports(
    request: Request,
    node_id: str | None = None,
    project_id: str | None = None,
    _auth: Any = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    uc: ListTrunkPorts = _container(request).list_trunk_ports
    ports = await uc.execute(
        node_id=NodeId(node_id) if node_id else None,
        project_id=ProjectId(project_id) if project_id else None,
    )
    return {"items": [_trunk_out(p).model_dump() for p in ports]}


@trunk_ports_router.get(
    "/trunk-ports/{port_id}",
    summary="Получить trunk-порт",
)
async def get_trunk_port(
    port_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_READ)),
) -> dict[str, Any]:
    uc: GetTrunkPort = _container(request).get_trunk_port
    port = await uc.execute(TrunkPortId(port_id))
    return {"trunk_port": _trunk_out(port).model_dump()}


@trunk_ports_router.patch(
    "/trunk-ports/{port_id}",
    summary="Обновить trunk-порт",
)
async def update_trunk_port(
    port_id: str,
    body: TrunkPortUpdateRequest,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> dict[str, Any]:
    uc: UpdateTrunkPort = _container(request).update_trunk_port
    port = await uc.execute(
        UpdateTrunkPortCommand(
            port_id=TrunkPortId(port_id),
            name=body.name,
            vlan_ids=body.vlan_ids,
            native_vlan=body.native_vlan,
            labels=body.labels,
        )
    )
    return {"trunk_port": _trunk_out(port).model_dump()}


@trunk_ports_router.delete(
    "/trunk-ports/{port_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить trunk-порт",
)
async def delete_trunk_port(
    port_id: str,
    request: Request,
    _auth: Any = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    uc: DeleteTrunkPort = _container(request).delete_trunk_port
    await uc.execute(TrunkPortId(port_id))
