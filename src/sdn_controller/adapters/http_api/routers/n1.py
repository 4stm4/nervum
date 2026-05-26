"""N1 REST routers — LogicalPort, SecurityGroup, AddressPool, ServiceObject, QosPolicy.

Node maintenance endpoints are appended to the nodes router file separately.
All routes use the shared ``require_permission`` dependency for RBAC.

Permissions used:

* ``NETWORK_READ``  — list / get (read-only)
* ``NETWORK_WRITE`` — create / update / attach / detach / add member
* ``ADMIN``         — delete / remove member
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status

from sdn_controller.adapters.http_api.auth import require as require_permission
from sdn_controller.app.container import Container
from sdn_controller.core.use_cases.n1 import (
    AddSecurityGroupMember,
    AttachLogicalPort,
    CreateAddressPool,
    CreateAddressPoolCommand,
    CreateLogicalPort,
    CreateLogicalPortCommand,
    CreateQosPolicy,
    CreateQosPolicyCommand,
    CreateSecurityGroup,
    CreateSecurityGroupCommand,
    CreateServiceObject,
    CreateServiceObjectCommand,
    DeleteAddressPool,
    DeleteLogicalPort,
    DeleteQosPolicy,
    DeleteSecurityGroup,
    DetachLogicalPort,
    GetAddressPool,
    GetLogicalPort,
    GetQosPolicy,
    GetSecurityGroup,
    GetServiceObject,
    ListAddressPools,
    ListLogicalPorts,
    ListQosPolicies,
    ListSecurityGroupMembers,
    ListSecurityGroups,
    ListServiceObjects,
    RemoveSecurityGroupMember,
    UpdateAddressPool,
    UpdateAddressPoolCommand,
    UpdateLogicalPort,
    UpdateLogicalPortCommand,
    UpdateQosPolicy,
    UpdateQosPolicyCommand,
    UpdateSecurityGroup,
    UpdateSecurityGroupCommand,
    UpdateServiceObject,
    UpdateServiceObjectCommand,
    DeleteServiceObject,
)
from sdn_controller.core.value_objects.ids import (
    AddressPoolId,
    LogicalPortId,
    NetworkId,
    NodeId,
    ProjectId,
    QosPolicyId,
    SecurityGroupId,
    ServiceObjectId,
)
from sdn_controller.core.value_objects.security import Permission


def _container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Inline Pydantic schemas
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field  # noqa: E402


class LogicalPortCreateRequest(BaseModel):
    name: str
    node_id: str
    network_id: str
    project_id: str | None = None
    mac_address: str | None = None
    ip_address: str | None = None
    vif_id: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class LogicalPortUpdateRequest(BaseModel):
    name: str | None = None
    labels: dict[str, str] | None = None


class AttachRequest(BaseModel):
    vif_id: str | None = None


class LogicalPortOut(BaseModel):
    id: str
    name: str
    node_id: str
    network_id: str
    status: str
    mac_address: str | None
    ip_address: str | None
    vif_id: str | None
    project_id: str | None
    labels: dict[str, str]
    created_at: str
    updated_at: str


class LogicalPortListResponse(BaseModel):
    items: list[LogicalPortOut]


class SecurityGroupCreateRequest(BaseModel):
    name: str
    description: str = ""
    project_id: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class SecurityGroupUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


class SecurityGroupOut(BaseModel):
    id: str
    name: str
    description: str
    project_id: str | None
    labels: dict[str, str]
    created_at: str
    updated_at: str


class SecurityGroupListResponse(BaseModel):
    items: list[SecurityGroupOut]


class MemberAddRequest(BaseModel):
    member_type: str
    member_value: str


class MemberOut(BaseModel):
    sg_id: str
    member_type: str
    member_value: str
    created_at: str


class MemberListResponse(BaseModel):
    items: list[MemberOut]


class AddressPoolCreateRequest(BaseModel):
    name: str
    description: str = ""
    project_id: str | None = None
    cidrs: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class AddressPoolUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    cidrs: list[str] | None = None
    labels: dict[str, str] | None = None


class AddressPoolOut(BaseModel):
    id: str
    name: str
    description: str
    project_id: str | None
    cidrs: list[str]
    labels: dict[str, str]
    created_at: str
    updated_at: str


class AddressPoolListResponse(BaseModel):
    items: list[AddressPoolOut]


class ServiceObjectCreateRequest(BaseModel):
    name: str
    protocol: str
    description: str = ""
    project_id: str | None = None
    ports: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class ServiceObjectUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    ports: list[str] | None = None
    labels: dict[str, str] | None = None


class ServiceObjectOut(BaseModel):
    id: str
    name: str
    description: str
    protocol: str
    project_id: str | None
    ports: list[str]
    labels: dict[str, str]
    created_at: str
    updated_at: str


class ServiceObjectListResponse(BaseModel):
    items: list[ServiceObjectOut]


class QosPolicyCreateRequest(BaseModel):
    name: str
    description: str = ""
    project_id: str | None = None
    ingress_kbps: int | None = None
    egress_kbps: int | None = None
    burst_kb: int | None = None
    dscp: int | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class QosPolicyUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    ingress_kbps: int | None = None
    egress_kbps: int | None = None
    burst_kb: int | None = None
    dscp: int | None = None
    labels: dict[str, str] | None = None


class QosPolicyOut(BaseModel):
    id: str
    name: str
    description: str
    project_id: str | None
    ingress_kbps: int | None
    egress_kbps: int | None
    burst_kb: int | None
    dscp: int | None
    labels: dict[str, str]
    created_at: str
    updated_at: str


class QosPolicyListResponse(BaseModel):
    items: list[QosPolicyOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _port_out(port: Any) -> LogicalPortOut:
    return LogicalPortOut(
        id=port.id,
        name=port.name,
        node_id=port.node_id,
        network_id=port.network_id,
        status=port.status.value if hasattr(port.status, "value") else port.status,
        mac_address=port.mac_address,
        ip_address=port.ip_address,
        vif_id=port.vif_id,
        project_id=port.project_id,
        labels=dict(port.labels),
        created_at=port.created_at.isoformat(),
        updated_at=port.updated_at.isoformat(),
    )


def _sg_out(sg: Any) -> SecurityGroupOut:
    return SecurityGroupOut(
        id=sg.id,
        name=sg.name,
        description=sg.description,
        project_id=sg.project_id,
        labels=dict(sg.labels),
        created_at=sg.created_at.isoformat(),
        updated_at=sg.updated_at.isoformat(),
    )


def _member_out(m: Any) -> MemberOut:
    return MemberOut(
        sg_id=m.sg_id,
        member_type=m.member_type,
        member_value=m.member_value,
        created_at=m.created_at.isoformat(),
    )


def _pool_out(pool: Any) -> AddressPoolOut:
    return AddressPoolOut(
        id=pool.id,
        name=pool.name,
        description=pool.description,
        project_id=pool.project_id,
        cidrs=list(pool.cidrs),
        labels=dict(pool.labels),
        created_at=pool.created_at.isoformat(),
        updated_at=pool.updated_at.isoformat(),
    )


def _svcobj_out(obj: Any) -> ServiceObjectOut:
    return ServiceObjectOut(
        id=obj.id,
        name=obj.name,
        description=obj.description,
        protocol=obj.protocol,
        project_id=obj.project_id,
        ports=list(obj.ports),
        labels=dict(obj.labels),
        created_at=obj.created_at.isoformat(),
        updated_at=obj.updated_at.isoformat(),
    )


def _qos_out(policy: Any) -> QosPolicyOut:
    return QosPolicyOut(
        id=policy.id,
        name=policy.name,
        description=policy.description,
        project_id=policy.project_id,
        ingress_kbps=policy.ingress_kbps,
        egress_kbps=policy.egress_kbps,
        burst_kb=policy.burst_kb,
        dscp=policy.dscp,
        labels=dict(policy.labels),
        created_at=policy.created_at.isoformat(),
        updated_at=policy.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Logical Ports router
# ---------------------------------------------------------------------------

logical_ports_router = APIRouter(prefix="/logical-ports", tags=["logical-ports"])


@logical_ports_router.get("", response_model=LogicalPortListResponse)
async def list_logical_ports(
    request: Request,
    node_id: str | None = None,
    network_id: str | None = None,
    project_id: str | None = None,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> LogicalPortListResponse:
    c = _container(request)
    ports = await c.list_logical_ports.execute(
        node_id=NodeId(node_id) if node_id else None,
        network_id=NetworkId(network_id) if network_id else None,
        project_id=ProjectId(project_id) if project_id else None,
    )
    return LogicalPortListResponse(items=[_port_out(p) for p in ports])


@logical_ports_router.post("", response_model=LogicalPortOut, status_code=status.HTTP_201_CREATED)
async def create_logical_port(
    body: LogicalPortCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> LogicalPortOut:
    c = _container(request)
    port = await c.create_logical_port.execute(
        CreateLogicalPortCommand(
            name=body.name,
            node_id=NodeId(body.node_id),
            network_id=NetworkId(body.network_id),
            project_id=ProjectId(body.project_id) if body.project_id else None,
            mac_address=body.mac_address,
            ip_address=body.ip_address,
            vif_id=body.vif_id,
            labels=body.labels,
        )
    )
    return _port_out(port)


@logical_ports_router.get("/{port_id}", response_model=LogicalPortOut)
async def get_logical_port(
    port_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> LogicalPortOut:
    c = _container(request)
    port = await c.get_logical_port.execute(LogicalPortId(port_id))
    return _port_out(port)


@logical_ports_router.patch("/{port_id}", response_model=LogicalPortOut)
async def update_logical_port(
    port_id: str,
    body: LogicalPortUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> LogicalPortOut:
    c = _container(request)
    port = await c.update_logical_port.execute(
        UpdateLogicalPortCommand(
            port_id=LogicalPortId(port_id),
            name=body.name,
            labels=body.labels,
        )
    )
    return _port_out(port)


@logical_ports_router.post("/{port_id}/attach", response_model=LogicalPortOut)
async def attach_logical_port(
    port_id: str,
    body: AttachRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> LogicalPortOut:
    c = _container(request)
    port = await c.attach_logical_port.execute(
        LogicalPortId(port_id), vif_id=body.vif_id
    )
    return _port_out(port)


@logical_ports_router.post("/{port_id}/detach", response_model=LogicalPortOut)
async def detach_logical_port(
    port_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> LogicalPortOut:
    c = _container(request)
    port = await c.detach_logical_port.execute(LogicalPortId(port_id))
    return _port_out(port)


@logical_ports_router.delete("/{port_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_logical_port(
    port_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c = _container(request)
    await c.delete_logical_port.execute(LogicalPortId(port_id))


# ---------------------------------------------------------------------------
# Security Groups router
# ---------------------------------------------------------------------------

security_groups_router = APIRouter(prefix="/security-groups", tags=["security-groups"])


@security_groups_router.get("", response_model=SecurityGroupListResponse)
async def list_security_groups(
    request: Request,
    project_id: str | None = None,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> SecurityGroupListResponse:
    c = _container(request)
    groups = await c.list_security_groups.execute(
        project_id=ProjectId(project_id) if project_id else None
    )
    return SecurityGroupListResponse(items=[_sg_out(sg) for sg in groups])


@security_groups_router.post(
    "", response_model=SecurityGroupOut, status_code=status.HTTP_201_CREATED
)
async def create_security_group(
    body: SecurityGroupCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> SecurityGroupOut:
    c = _container(request)
    sg = await c.create_security_group.execute(
        CreateSecurityGroupCommand(
            name=body.name,
            description=body.description,
            project_id=ProjectId(body.project_id) if body.project_id else None,
            labels=body.labels,
        )
    )
    return _sg_out(sg)


@security_groups_router.get("/{sg_id}", response_model=SecurityGroupOut)
async def get_security_group(
    sg_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> SecurityGroupOut:
    c = _container(request)
    sg = await c.get_security_group.execute(SecurityGroupId(sg_id))
    return _sg_out(sg)


@security_groups_router.patch("/{sg_id}", response_model=SecurityGroupOut)
async def update_security_group(
    sg_id: str,
    body: SecurityGroupUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> SecurityGroupOut:
    c = _container(request)
    sg = await c.update_security_group.execute(
        UpdateSecurityGroupCommand(
            sg_id=SecurityGroupId(sg_id),
            name=body.name,
            description=body.description,
            labels=body.labels,
        )
    )
    return _sg_out(sg)


@security_groups_router.delete("/{sg_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_security_group(
    sg_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c = _container(request)
    await c.delete_security_group.execute(SecurityGroupId(sg_id))


@security_groups_router.get("/{sg_id}/members", response_model=MemberListResponse)
async def list_sg_members(
    sg_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> MemberListResponse:
    c = _container(request)
    members = await c.list_security_group_members.execute(SecurityGroupId(sg_id))
    return MemberListResponse(items=[_member_out(m) for m in members])


@security_groups_router.post(
    "/{sg_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED
)
async def add_sg_member(
    sg_id: str,
    body: MemberAddRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> MemberOut:
    c = _container(request)
    member = await c.add_security_group_member.execute(
        SecurityGroupId(sg_id), body.member_type, body.member_value
    )
    return _member_out(member)


@security_groups_router.delete(
    "/{sg_id}/members/{member_type}/{member_value}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_sg_member(
    sg_id: str,
    member_type: str,
    member_value: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c = _container(request)
    await c.remove_security_group_member.execute(
        SecurityGroupId(sg_id), member_type, member_value
    )


# ---------------------------------------------------------------------------
# Address Pools router
# ---------------------------------------------------------------------------

address_pools_router = APIRouter(prefix="/address-pools", tags=["address-pools"])


@address_pools_router.get("", response_model=AddressPoolListResponse)
async def list_address_pools(
    request: Request,
    project_id: str | None = None,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> AddressPoolListResponse:
    c = _container(request)
    pools = await c.list_address_pools.execute(
        project_id=ProjectId(project_id) if project_id else None
    )
    return AddressPoolListResponse(items=[_pool_out(p) for p in pools])


@address_pools_router.post(
    "", response_model=AddressPoolOut, status_code=status.HTTP_201_CREATED
)
async def create_address_pool(
    body: AddressPoolCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> AddressPoolOut:
    c = _container(request)
    pool = await c.create_address_pool.execute(
        CreateAddressPoolCommand(
            name=body.name,
            description=body.description,
            project_id=ProjectId(body.project_id) if body.project_id else None,
            cidrs=tuple(body.cidrs),
            labels=body.labels,
        )
    )
    return _pool_out(pool)


@address_pools_router.get("/{pool_id}", response_model=AddressPoolOut)
async def get_address_pool(
    pool_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> AddressPoolOut:
    c = _container(request)
    pool = await c.get_address_pool.execute(AddressPoolId(pool_id))
    return _pool_out(pool)


@address_pools_router.patch("/{pool_id}", response_model=AddressPoolOut)
async def update_address_pool(
    pool_id: str,
    body: AddressPoolUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> AddressPoolOut:
    c = _container(request)
    pool = await c.update_address_pool.execute(
        UpdateAddressPoolCommand(
            pool_id=AddressPoolId(pool_id),
            name=body.name,
            description=body.description,
            cidrs=tuple(body.cidrs) if body.cidrs is not None else None,
            labels=body.labels,
        )
    )
    return _pool_out(pool)


@address_pools_router.delete("/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_address_pool(
    pool_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c = _container(request)
    await c.delete_address_pool.execute(AddressPoolId(pool_id))


# ---------------------------------------------------------------------------
# Service Objects router
# ---------------------------------------------------------------------------

service_objects_router = APIRouter(prefix="/service-objects", tags=["service-objects"])


@service_objects_router.get("", response_model=ServiceObjectListResponse)
async def list_service_objects(
    request: Request,
    project_id: str | None = None,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> ServiceObjectListResponse:
    c = _container(request)
    objects = await c.list_service_objects.execute(
        project_id=ProjectId(project_id) if project_id else None
    )
    return ServiceObjectListResponse(items=[_svcobj_out(o) for o in objects])


@service_objects_router.post(
    "", response_model=ServiceObjectOut, status_code=status.HTTP_201_CREATED
)
async def create_service_object(
    body: ServiceObjectCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> ServiceObjectOut:
    c = _container(request)
    obj = await c.create_service_object.execute(
        CreateServiceObjectCommand(
            name=body.name,
            protocol=body.protocol,
            description=body.description,
            project_id=ProjectId(body.project_id) if body.project_id else None,
            ports=tuple(body.ports),
            labels=body.labels,
        )
    )
    return _svcobj_out(obj)


@service_objects_router.get("/{obj_id}", response_model=ServiceObjectOut)
async def get_service_object(
    obj_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> ServiceObjectOut:
    c = _container(request)
    obj = await c.get_service_object.execute(ServiceObjectId(obj_id))
    return _svcobj_out(obj)


@service_objects_router.patch("/{obj_id}", response_model=ServiceObjectOut)
async def update_service_object(
    obj_id: str,
    body: ServiceObjectUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> ServiceObjectOut:
    c = _container(request)
    obj = await c.update_service_object.execute(
        UpdateServiceObjectCommand(
            obj_id=ServiceObjectId(obj_id),
            name=body.name,
            description=body.description,
            ports=tuple(body.ports) if body.ports is not None else None,
            labels=body.labels,
        )
    )
    return _svcobj_out(obj)


@service_objects_router.delete("/{obj_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service_object(
    obj_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c = _container(request)
    await c.delete_service_object.execute(ServiceObjectId(obj_id))


# ---------------------------------------------------------------------------
# QoS Policies router
# ---------------------------------------------------------------------------

qos_policies_router = APIRouter(prefix="/qos-policies", tags=["qos-policies"])


@qos_policies_router.get("", response_model=QosPolicyListResponse)
async def list_qos_policies(
    request: Request,
    project_id: str | None = None,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> QosPolicyListResponse:
    c = _container(request)
    policies = await c.list_qos_policies.execute(
        project_id=ProjectId(project_id) if project_id else None
    )
    return QosPolicyListResponse(items=[_qos_out(p) for p in policies])


@qos_policies_router.post(
    "", response_model=QosPolicyOut, status_code=status.HTTP_201_CREATED
)
async def create_qos_policy(
    body: QosPolicyCreateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> QosPolicyOut:
    c = _container(request)
    policy = await c.create_qos_policy.execute(
        CreateQosPolicyCommand(
            name=body.name,
            description=body.description,
            project_id=ProjectId(body.project_id) if body.project_id else None,
            ingress_kbps=body.ingress_kbps,
            egress_kbps=body.egress_kbps,
            burst_kb=body.burst_kb,
            dscp=body.dscp,
            labels=body.labels,
        )
    )
    return _qos_out(policy)


@qos_policies_router.get("/{policy_id}", response_model=QosPolicyOut)
async def get_qos_policy(
    policy_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_READ)),
) -> QosPolicyOut:
    c = _container(request)
    policy = await c.get_qos_policy.execute(QosPolicyId(policy_id))
    return _qos_out(policy)


@qos_policies_router.patch("/{policy_id}", response_model=QosPolicyOut)
async def update_qos_policy(
    policy_id: str,
    body: QosPolicyUpdateRequest,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> QosPolicyOut:
    c = _container(request)
    policy = await c.update_qos_policy.execute(
        UpdateQosPolicyCommand(
            policy_id=QosPolicyId(policy_id),
            name=body.name,
            description=body.description,
            ingress_kbps=body.ingress_kbps,
            egress_kbps=body.egress_kbps,
            burst_kb=body.burst_kb,
            dscp=body.dscp,
            labels=body.labels,
        )
    )
    return _qos_out(policy)


@qos_policies_router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_qos_policy(
    policy_id: str,
    request: Request,
    _: None = Depends(require_permission(Permission.NETWORK_WRITE)),
) -> None:
    c = _container(request)
    await c.delete_qos_policy.execute(QosPolicyId(policy_id))
