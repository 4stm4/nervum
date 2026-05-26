"""N1 use cases — LogicalPort, SecurityGroup, AddressPool, ServiceObject, QosPolicy,
and Node maintenance mode.

All mutating use cases emit an outbox event (N1-08) after the save so that
subscribers see created / updated / deleted lifecycle events.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime

from sdn_controller.core.entities import (
    AddressPool,
    LogicalPort,
    QosPolicy,
    SecurityGroup,
    SecurityGroupMember,
    ServiceObject,
)
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.value_objects.enums import LogicalPortStatus
from sdn_controller.core.value_objects.errors import NotFoundError, ValidationError
from sdn_controller.core.value_objects.ids import (
    AddressPoolId,
    IdFactory,
    LogicalPortId,
    NetworkId,
    NodeId,
    ProjectId,
    QosPolicyId,
    SecurityGroupId,
    ServiceObjectId,
)
from sdn_controller.ports.persistence import (
    AddressPoolRepository,
    LogicalPortRepository,
    NetworkRepository,
    NodeRepository,
    QosPolicyRepository,
    SecurityGroupMemberRepository,
    SecurityGroupRepository,
    ServiceObjectRepository,
)


def _generate_mac() -> str:
    """Generate a random locally-administered unicast MAC address."""
    raw = secrets.token_bytes(5)
    # First byte: set bit 1 (locally administered), clear bit 0 (unicast) → 0x02
    return "02:" + ":".join(f"{b:02x}" for b in raw)


# ---------------------------------------------------------------------------
# LogicalPort use cases (N1-01)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateLogicalPortCommand:
    name: str
    node_id: NodeId
    network_id: NetworkId
    project_id: ProjectId | None = None
    mac_address: str | None = None   # auto-generated if None
    ip_address: str | None = None
    vif_id: str | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class UpdateLogicalPortCommand:
    port_id: LogicalPortId
    name: str | None = None
    labels: dict[str, str] | None = None


class CreateLogicalPort:
    def __init__(
        self,
        *,
        ports: LogicalPortRepository,
        nodes: NodeRepository,
        networks: NetworkRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._ports = ports
        self._nodes = nodes
        self._networks = networks
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateLogicalPortCommand) -> LogicalPort:
        if await self._nodes.get(cmd.node_id) is None:
            raise NotFoundError(f"node {cmd.node_id} not found")
        if await self._networks.get(cmd.network_id) is None:
            raise NotFoundError(f"network {cmd.network_id} not found")

        now = self._clock.now()
        mac = cmd.mac_address or _generate_mac()
        port = LogicalPort(
            id=self._ids.logical_port(),
            name=cmd.name,
            node_id=cmd.node_id,
            network_id=cmd.network_id,
            project_id=cmd.project_id,
            mac_address=mac,
            ip_address=cmd.ip_address,
            vif_id=cmd.vif_id,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._ports.save(port)
        await self._events.publish(
            event_type="logical_port.created",
            resource_type="logical_port",
            resource_id=port.id,
            payload={"name": port.name, "node_id": port.node_id, "network_id": port.network_id},
            project_id=port.project_id,
        )
        return port


class GetLogicalPort:
    def __init__(self, *, ports: LogicalPortRepository) -> None:
        self._ports = ports

    async def execute(self, port_id: LogicalPortId) -> LogicalPort:
        port = await self._ports.get(port_id)
        if port is None:
            raise NotFoundError(f"logical port {port_id} not found")
        return port


class ListLogicalPorts:
    def __init__(self, *, ports: LogicalPortRepository) -> None:
        self._ports = ports

    async def execute(
        self,
        *,
        node_id: NodeId | None = None,
        network_id: NetworkId | None = None,
        project_id: ProjectId | None = None,
    ) -> list[LogicalPort]:
        return await self._ports.list(
            node_id=node_id, network_id=network_id, project_id=project_id
        )


class UpdateLogicalPort:
    def __init__(
        self,
        *,
        ports: LogicalPortRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._ports = ports
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateLogicalPortCommand) -> LogicalPort:
        port = await self._ports.get(cmd.port_id)
        if port is None:
            raise NotFoundError(f"logical port {cmd.port_id} not found")
        port.update(name=cmd.name, labels=cmd.labels, now=self._clock.now())
        await self._ports.save(port)
        await self._events.publish(
            event_type="logical_port.updated",
            resource_type="logical_port",
            resource_id=port.id,
            project_id=port.project_id,
        )
        return port


class AttachLogicalPort:
    def __init__(
        self,
        *,
        ports: LogicalPortRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._ports = ports
        self._clock = clock
        self._events = events

    async def execute(
        self, port_id: LogicalPortId, *, vif_id: str | None = None
    ) -> LogicalPort:
        port = await self._ports.get(port_id)
        if port is None:
            raise NotFoundError(f"logical port {port_id} not found")
        port.attach(vif_id=vif_id, now=self._clock.now())
        await self._ports.save(port)
        await self._events.publish(
            event_type="logical_port.attached",
            resource_type="logical_port",
            resource_id=port.id,
            project_id=port.project_id,
        )
        return port


class DetachLogicalPort:
    def __init__(
        self,
        *,
        ports: LogicalPortRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._ports = ports
        self._clock = clock
        self._events = events

    async def execute(self, port_id: LogicalPortId) -> LogicalPort:
        port = await self._ports.get(port_id)
        if port is None:
            raise NotFoundError(f"logical port {port_id} not found")
        port.detach(now=self._clock.now())
        await self._ports.save(port)
        await self._events.publish(
            event_type="logical_port.detached",
            resource_type="logical_port",
            resource_id=port.id,
            project_id=port.project_id,
        )
        return port


class DeleteLogicalPort:
    def __init__(
        self,
        *,
        ports: LogicalPortRepository,
        events: EventPublisher,
    ) -> None:
        self._ports = ports
        self._events = events

    async def execute(self, port_id: LogicalPortId) -> None:
        port = await self._ports.get(port_id)
        if port is None:
            raise NotFoundError(f"logical port {port_id} not found")
        await self._ports.delete(port_id)
        await self._events.publish(
            event_type="logical_port.deleted",
            resource_type="logical_port",
            resource_id=port_id,
            project_id=port.project_id,
        )


# ---------------------------------------------------------------------------
# SecurityGroup use cases (N1-02)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateSecurityGroupCommand:
    name: str
    description: str = ""
    project_id: ProjectId | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class UpdateSecurityGroupCommand:
    sg_id: SecurityGroupId
    name: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


class CreateSecurityGroup:
    def __init__(
        self,
        *,
        groups: SecurityGroupRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._groups = groups
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateSecurityGroupCommand) -> SecurityGroup:
        now = self._clock.now()
        sg = SecurityGroup(
            id=self._ids.security_group(),
            name=cmd.name,
            description=cmd.description,
            project_id=cmd.project_id,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._groups.save(sg)
        await self._events.publish(
            event_type="security_group.created",
            resource_type="security_group",
            resource_id=sg.id,
            payload={"name": sg.name},
            project_id=sg.project_id,
        )
        return sg


class GetSecurityGroup:
    def __init__(self, *, groups: SecurityGroupRepository) -> None:
        self._groups = groups

    async def execute(self, sg_id: SecurityGroupId) -> SecurityGroup:
        sg = await self._groups.get(sg_id)
        if sg is None:
            raise NotFoundError(f"security group {sg_id} not found")
        return sg


class ListSecurityGroups:
    def __init__(self, *, groups: SecurityGroupRepository) -> None:
        self._groups = groups

    async def execute(
        self, *, project_id: ProjectId | None = None
    ) -> list[SecurityGroup]:
        return await self._groups.list(project_id=project_id)


class UpdateSecurityGroup:
    def __init__(
        self,
        *,
        groups: SecurityGroupRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._groups = groups
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateSecurityGroupCommand) -> SecurityGroup:
        sg = await self._groups.get(cmd.sg_id)
        if sg is None:
            raise NotFoundError(f"security group {cmd.sg_id} not found")
        sg.update(
            name=cmd.name,
            description=cmd.description,
            labels=cmd.labels,
            now=self._clock.now(),
        )
        await self._groups.save(sg)
        await self._events.publish(
            event_type="security_group.updated",
            resource_type="security_group",
            resource_id=sg.id,
            project_id=sg.project_id,
        )
        return sg


class DeleteSecurityGroup:
    def __init__(
        self,
        *,
        groups: SecurityGroupRepository,
        members: SecurityGroupMemberRepository,
        events: EventPublisher,
    ) -> None:
        self._groups = groups
        self._members = members
        self._events = events

    async def execute(self, sg_id: SecurityGroupId) -> None:
        sg = await self._groups.get(sg_id)
        if sg is None:
            raise NotFoundError(f"security group {sg_id} not found")
        await self._members.delete_for_group(sg_id)
        await self._groups.delete(sg_id)
        await self._events.publish(
            event_type="security_group.deleted",
            resource_type="security_group",
            resource_id=sg_id,
            project_id=sg.project_id,
        )


class AddSecurityGroupMember:
    def __init__(
        self,
        *,
        groups: SecurityGroupRepository,
        members: SecurityGroupMemberRepository,
        clock: Clock,
    ) -> None:
        self._groups = groups
        self._members = members
        self._clock = clock

    async def execute(
        self,
        sg_id: SecurityGroupId,
        member_type: str,
        member_value: str,
    ) -> SecurityGroupMember:
        if await self._groups.get(sg_id) is None:
            raise NotFoundError(f"security group {sg_id} not found")
        member = SecurityGroupMember(
            sg_id=sg_id,
            member_type=member_type,
            member_value=member_value,
            created_at=self._clock.now(),
        )
        await self._members.add(member)
        return member


class RemoveSecurityGroupMember:
    def __init__(
        self,
        *,
        groups: SecurityGroupRepository,
        members: SecurityGroupMemberRepository,
    ) -> None:
        self._groups = groups
        self._members = members

    async def execute(
        self,
        sg_id: SecurityGroupId,
        member_type: str,
        member_value: str,
    ) -> None:
        if await self._groups.get(sg_id) is None:
            raise NotFoundError(f"security group {sg_id} not found")
        await self._members.remove(sg_id, member_type, member_value)


class ListSecurityGroupMembers:
    def __init__(self, *, members: SecurityGroupMemberRepository) -> None:
        self._members = members

    async def execute(self, sg_id: SecurityGroupId) -> list[SecurityGroupMember]:
        return await self._members.list_for_group(sg_id)


# ---------------------------------------------------------------------------
# AddressPool use cases (N1-03)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateAddressPoolCommand:
    name: str
    description: str = ""
    project_id: ProjectId | None = None
    cidrs: tuple[str, ...] = ()
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class UpdateAddressPoolCommand:
    pool_id: AddressPoolId
    name: str | None = None
    description: str | None = None
    cidrs: tuple[str, ...] | None = None
    labels: dict[str, str] | None = None


class CreateAddressPool:
    def __init__(
        self,
        *,
        pools: AddressPoolRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._pools = pools
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateAddressPoolCommand) -> AddressPool:
        now = self._clock.now()
        pool = AddressPool(
            id=self._ids.address_pool(),
            name=cmd.name,
            description=cmd.description,
            project_id=cmd.project_id,
            cidrs=tuple(cmd.cidrs),
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._pools.save(pool)
        await self._events.publish(
            event_type="address_pool.created",
            resource_type="address_pool",
            resource_id=pool.id,
            payload={"name": pool.name},
            project_id=pool.project_id,
        )
        return pool


class GetAddressPool:
    def __init__(self, *, pools: AddressPoolRepository) -> None:
        self._pools = pools

    async def execute(self, pool_id: AddressPoolId) -> AddressPool:
        pool = await self._pools.get(pool_id)
        if pool is None:
            raise NotFoundError(f"address pool {pool_id} not found")
        return pool


class ListAddressPools:
    def __init__(self, *, pools: AddressPoolRepository) -> None:
        self._pools = pools

    async def execute(
        self, *, project_id: ProjectId | None = None
    ) -> list[AddressPool]:
        return await self._pools.list(project_id=project_id)


class UpdateAddressPool:
    def __init__(
        self,
        *,
        pools: AddressPoolRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._pools = pools
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateAddressPoolCommand) -> AddressPool:
        pool = await self._pools.get(cmd.pool_id)
        if pool is None:
            raise NotFoundError(f"address pool {cmd.pool_id} not found")
        pool.update(
            name=cmd.name,
            description=cmd.description,
            cidrs=cmd.cidrs,
            labels=cmd.labels,
            now=self._clock.now(),
        )
        await self._pools.save(pool)
        await self._events.publish(
            event_type="address_pool.updated",
            resource_type="address_pool",
            resource_id=pool.id,
            project_id=pool.project_id,
        )
        return pool


class DeleteAddressPool:
    def __init__(
        self,
        *,
        pools: AddressPoolRepository,
        events: EventPublisher,
    ) -> None:
        self._pools = pools
        self._events = events

    async def execute(self, pool_id: AddressPoolId) -> None:
        pool = await self._pools.get(pool_id)
        if pool is None:
            raise NotFoundError(f"address pool {pool_id} not found")
        await self._pools.delete(pool_id)
        await self._events.publish(
            event_type="address_pool.deleted",
            resource_type="address_pool",
            resource_id=pool_id,
            project_id=pool.project_id,
        )


# ---------------------------------------------------------------------------
# ServiceObject use cases (N1-04)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateServiceObjectCommand:
    name: str
    protocol: str
    description: str = ""
    project_id: ProjectId | None = None
    ports: tuple[str, ...] = ()
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class UpdateServiceObjectCommand:
    obj_id: ServiceObjectId
    name: str | None = None
    description: str | None = None
    ports: tuple[str, ...] | None = None
    labels: dict[str, str] | None = None


class CreateServiceObject:
    def __init__(
        self,
        *,
        objects: ServiceObjectRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._objects = objects
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateServiceObjectCommand) -> ServiceObject:
        now = self._clock.now()
        obj = ServiceObject(
            id=self._ids.service_object(),
            name=cmd.name,
            protocol=cmd.protocol,
            description=cmd.description,
            project_id=cmd.project_id,
            ports=tuple(cmd.ports),
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._objects.save(obj)
        await self._events.publish(
            event_type="service_object.created",
            resource_type="service_object",
            resource_id=obj.id,
            payload={"name": obj.name, "protocol": obj.protocol},
            project_id=obj.project_id,
        )
        return obj


class GetServiceObject:
    def __init__(self, *, objects: ServiceObjectRepository) -> None:
        self._objects = objects

    async def execute(self, obj_id: ServiceObjectId) -> ServiceObject:
        obj = await self._objects.get(obj_id)
        if obj is None:
            raise NotFoundError(f"service object {obj_id} not found")
        return obj


class ListServiceObjects:
    def __init__(self, *, objects: ServiceObjectRepository) -> None:
        self._objects = objects

    async def execute(
        self, *, project_id: ProjectId | None = None
    ) -> list[ServiceObject]:
        return await self._objects.list(project_id=project_id)


class UpdateServiceObject:
    def __init__(
        self,
        *,
        objects: ServiceObjectRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._objects = objects
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateServiceObjectCommand) -> ServiceObject:
        obj = await self._objects.get(cmd.obj_id)
        if obj is None:
            raise NotFoundError(f"service object {cmd.obj_id} not found")
        obj.update(
            name=cmd.name,
            description=cmd.description,
            ports=cmd.ports,
            labels=cmd.labels,
            now=self._clock.now(),
        )
        await self._objects.save(obj)
        await self._events.publish(
            event_type="service_object.updated",
            resource_type="service_object",
            resource_id=obj.id,
            project_id=obj.project_id,
        )
        return obj


class DeleteServiceObject:
    def __init__(
        self,
        *,
        objects: ServiceObjectRepository,
        events: EventPublisher,
    ) -> None:
        self._objects = objects
        self._events = events

    async def execute(self, obj_id: ServiceObjectId) -> None:
        obj = await self._objects.get(obj_id)
        if obj is None:
            raise NotFoundError(f"service object {obj_id} not found")
        await self._objects.delete(obj_id)
        await self._events.publish(
            event_type="service_object.deleted",
            resource_type="service_object",
            resource_id=obj_id,
            project_id=obj.project_id,
        )


# ---------------------------------------------------------------------------
# QosPolicy use cases (N1-05)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateQosPolicyCommand:
    name: str
    description: str = ""
    project_id: ProjectId | None = None
    ingress_kbps: int | None = None
    egress_kbps: int | None = None
    burst_kb: int | None = None
    dscp: int | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True)
class UpdateQosPolicyCommand:
    policy_id: QosPolicyId
    name: str | None = None
    description: str | None = None
    ingress_kbps: int | None = None
    egress_kbps: int | None = None
    burst_kb: int | None = None
    dscp: int | None = None
    labels: dict[str, str] | None = None


class CreateQosPolicy:
    def __init__(
        self,
        *,
        policies: QosPolicyRepository,
        clock: Clock,
        ids: IdFactory,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._clock = clock
        self._ids = ids
        self._events = events

    async def execute(self, cmd: CreateQosPolicyCommand) -> QosPolicy:
        now = self._clock.now()
        policy = QosPolicy(
            id=self._ids.qos_policy(),
            name=cmd.name,
            description=cmd.description,
            project_id=cmd.project_id,
            ingress_kbps=cmd.ingress_kbps,
            egress_kbps=cmd.egress_kbps,
            burst_kb=cmd.burst_kb,
            dscp=cmd.dscp,
            labels=dict(cmd.labels or {}),
            created_at=now,
            updated_at=now,
        )
        await self._policies.save(policy)
        await self._events.publish(
            event_type="qos_policy.created",
            resource_type="qos_policy",
            resource_id=policy.id,
            payload={"name": policy.name},
            project_id=policy.project_id,
        )
        return policy


class GetQosPolicy:
    def __init__(self, *, policies: QosPolicyRepository) -> None:
        self._policies = policies

    async def execute(self, policy_id: QosPolicyId) -> QosPolicy:
        policy = await self._policies.get(policy_id)
        if policy is None:
            raise NotFoundError(f"QoS policy {policy_id} not found")
        return policy


class ListQosPolicies:
    def __init__(self, *, policies: QosPolicyRepository) -> None:
        self._policies = policies

    async def execute(
        self, *, project_id: ProjectId | None = None
    ) -> list[QosPolicy]:
        return await self._policies.list(project_id=project_id)


class UpdateQosPolicy:
    def __init__(
        self,
        *,
        policies: QosPolicyRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._clock = clock
        self._events = events

    async def execute(self, cmd: UpdateQosPolicyCommand) -> QosPolicy:
        policy = await self._policies.get(cmd.policy_id)
        if policy is None:
            raise NotFoundError(f"QoS policy {cmd.policy_id} not found")
        policy.update(
            name=cmd.name,
            description=cmd.description,
            ingress_kbps=cmd.ingress_kbps,
            egress_kbps=cmd.egress_kbps,
            burst_kb=cmd.burst_kb,
            dscp=cmd.dscp,
            labels=cmd.labels,
            now=self._clock.now(),
        )
        await self._policies.save(policy)
        await self._events.publish(
            event_type="qos_policy.updated",
            resource_type="qos_policy",
            resource_id=policy.id,
            project_id=policy.project_id,
        )
        return policy


class DeleteQosPolicy:
    def __init__(
        self,
        *,
        policies: QosPolicyRepository,
        events: EventPublisher,
    ) -> None:
        self._policies = policies
        self._events = events

    async def execute(self, policy_id: QosPolicyId) -> None:
        policy = await self._policies.get(policy_id)
        if policy is None:
            raise NotFoundError(f"QoS policy {policy_id} not found")
        await self._policies.delete(policy_id)
        await self._events.publish(
            event_type="qos_policy.deleted",
            resource_type="qos_policy",
            resource_id=policy_id,
            project_id=policy.project_id,
        )


# ---------------------------------------------------------------------------
# Node maintenance mode (N1-06)
# ---------------------------------------------------------------------------


class EnterMaintenanceMode:
    def __init__(
        self,
        *,
        nodes: NodeRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._nodes = nodes
        self._clock = clock
        self._events = events

    async def execute(self, node_id: NodeId) -> None:
        from sdn_controller.core.value_objects.ids import NodeId as _NodeId  # noqa: F401

        node = await self._nodes.get(node_id)
        if node is None:
            raise NotFoundError(f"node {node_id} not found")
        node.enter_maintenance(now=self._clock.now())
        await self._nodes.save(node)
        await self._events.publish(
            event_type="node.maintenance_entered",
            resource_type="node",
            resource_id=node_id,
            project_id=node.project_id,
        )


class ExitMaintenanceMode:
    def __init__(
        self,
        *,
        nodes: NodeRepository,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._nodes = nodes
        self._clock = clock
        self._events = events

    async def execute(self, node_id: NodeId) -> None:
        node = await self._nodes.get(node_id)
        if node is None:
            raise NotFoundError(f"node {node_id} not found")
        node.exit_maintenance(now=self._clock.now())
        await self._nodes.save(node)
        await self._events.publish(
            event_type="node.maintenance_exited",
            resource_type="node",
            resource_id=node_id,
            project_id=node.project_id,
        )


__all__ = [
    # Commands
    "CreateLogicalPortCommand",
    "UpdateLogicalPortCommand",
    "CreateSecurityGroupCommand",
    "UpdateSecurityGroupCommand",
    "CreateAddressPoolCommand",
    "UpdateAddressPoolCommand",
    "CreateServiceObjectCommand",
    "UpdateServiceObjectCommand",
    "CreateQosPolicyCommand",
    "UpdateQosPolicyCommand",
    # Use cases
    "AttachLogicalPort",
    "CreateLogicalPort",
    "DeleteLogicalPort",
    "DetachLogicalPort",
    "GetLogicalPort",
    "ListLogicalPorts",
    "UpdateLogicalPort",
    "AddSecurityGroupMember",
    "CreateSecurityGroup",
    "DeleteSecurityGroup",
    "GetSecurityGroup",
    "ListSecurityGroupMembers",
    "ListSecurityGroups",
    "RemoveSecurityGroupMember",
    "UpdateSecurityGroup",
    "CreateAddressPool",
    "DeleteAddressPool",
    "GetAddressPool",
    "ListAddressPools",
    "UpdateAddressPool",
    "CreateServiceObject",
    "DeleteServiceObject",
    "GetServiceObject",
    "ListServiceObjects",
    "UpdateServiceObject",
    "CreateQosPolicy",
    "DeleteQosPolicy",
    "GetQosPolicy",
    "ListQosPolicies",
    "UpdateQosPolicy",
    "EnterMaintenanceMode",
    "ExitMaintenanceMode",
]
