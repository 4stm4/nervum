"""Mapping functions between SQL rows and domain aggregates.

Keeping the conversion in one place means the ORM models stay an
adapter-internal detail — repositories accept and return pure dataclasses,
exactly like the in-memory adapter does.
"""

from __future__ import annotations

from typing import Any

from sdn_controller.adapters.sql import models
from sdn_controller.core.entities import (
    AddressPool,
    AuditEvent,
    EnrollmentToken,
    IpAllocation,
    LogicalPort,
    Network,
    Node,
    NodeSnapshot,
    ObservedBridge,
    ObservedInterface,
    ObservedPort,
    ObservedState,
    Operation,
    OperationError,
    OperationEvent,
    OutboxEvent,
    Project,
    ProjectMember,
    QosPolicy,
    ResourceRef,
    SecurityGroup,
    SecurityGroupMember,
    SecurityPolicy,
    SecurityPolicyRule,
    ServiceAccount,
    ServiceObject,
    ServiceToken,
    Subnet,
    TrunkPort,
    WebhookSubscription,
)
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.edge_services import (
    DhcpSpec,
    FirewallAction,
    FirewallPolicy,
    FirewallProto,
    FirewallRule,
    NatSpec,
)
from sdn_controller.core.value_objects.enums import (
    LogicalPortStatus,
    NetworkType,
    NodeStatus,
    OperationKind,
    OperationStatus,
    SecurityPolicyStatus,
    WebhookSubscriptionState,
)
from sdn_controller.core.value_objects.ids import (
    AddressPoolId,
    AuditEventId,
    EnrollmentTokenId,
    IpAllocationId,
    LogicalPortId,
    NetworkId,
    NodeId,
    NodeSnapshotId,
    OperationId,
    OutboxEventId,
    ProjectId,
    QosPolicyId,
    SecurityGroupId,
    SecurityPolicyId,
    ServiceAccountId,
    ServiceObjectId,
    ServiceTokenId,
    SubnetId,
    TrunkPortId,
    WebhookSubscriptionId,
)
from sdn_controller.core.value_objects.ipam import (
    IpAllocationKind,
    IpRange,
    OwnerRef,
)
from sdn_controller.core.value_objects.security import Role


def capabilities_to_json(caps: NodeCapabilities | None) -> dict[str, Any] | None:
    if caps is None:
        return None
    return {
        "ovs_version": caps.ovs_version,
        "kernel": caps.kernel,
        "interfaces": list(caps.interfaces),
        "features": list(caps.features),
    }


def capabilities_from_json(blob: dict[str, Any] | None) -> NodeCapabilities | None:
    if blob is None:
        return None
    return NodeCapabilities(
        ovs_version=blob.get("ovs_version"),
        kernel=blob.get("kernel"),
        interfaces=tuple(blob.get("interfaces") or ()),
        features=tuple(blob.get("features") or ()),
    )


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


def node_to_row(node: Node) -> models.NodeRow:
    return models.NodeRow(
        id=node.id,
        name=node.name,
        mgmt_ip=node.mgmt_ip,
        status=node.status.value,
        roles=list(node.roles),
        labels=dict(node.labels),
        agent_version=node.agent_version,
        last_seen_at=node.last_seen_at,
        capabilities=capabilities_to_json(node.capabilities),
        tls_thumbprint=node.tls_thumbprint,
        project_id=node.project_id,
        maintenance=node.maintenance,
        maintenance_at=node.maintenance_at,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def node_from_row(row: models.NodeRow) -> Node:
    return Node(
        id=NodeId(row.id),
        name=row.name,
        mgmt_ip=row.mgmt_ip,
        status=NodeStatus(row.status),
        roles=list(row.roles),
        labels=dict(row.labels),
        agent_version=row.agent_version,
        last_seen_at=row.last_seen_at,
        capabilities=capabilities_from_json(row.capabilities),
        tls_thumbprint=row.tls_thumbprint,
        project_id=ProjectId(row.project_id) if row.project_id else None,
        maintenance=getattr(row, "maintenance", False) or False,
        maintenance_at=getattr(row, "maintenance_at", None),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# EnrollmentToken
# ---------------------------------------------------------------------------


def enrollment_token_to_row(token: EnrollmentToken) -> models.EnrollmentTokenRow:
    return models.EnrollmentTokenRow(
        id=token.id,
        node_id=token.node_id,
        token_hash=token.token_hash,
        issued_at=token.issued_at,
        expires_at=token.expires_at,
        used_at=token.used_at,
        issued_by=token.issued_by,
    )


def enrollment_token_from_row(row: models.EnrollmentTokenRow) -> EnrollmentToken:
    return EnrollmentToken(
        id=EnrollmentTokenId(row.id),
        node_id=NodeId(row.node_id),
        token_hash=row.token_hash,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        used_at=row.used_at,
        issued_by=row.issued_by,
    )


# ---------------------------------------------------------------------------
# Network / Subnet
# ---------------------------------------------------------------------------


def network_to_row(network: Network) -> models.NetworkRow:
    row = models.NetworkRow(
        id=network.id,
        name=network.name,
        type=network.type.value,
        mtu=network.mtu,
        vlan_id=network.vlan_id,
        vni=network.vni,
        labels=dict(network.labels),
        intent_version=network.intent_version,
        node_ids=list(network.node_ids),
        spec_hash=network.spec_hash,
        nat=_nat_to_json(network.nat),
        firewall_policy=_firewall_to_json(network.firewall_policy),
        project_id=network.project_id,
        created_at=network.created_at,
        updated_at=network.updated_at,
    )
    if network.subnet is not None:
        row.subnet = subnet_to_row(network.subnet, network_id=network.id)
    return row


def subnet_to_row(subnet: Subnet, *, network_id: str) -> models.SubnetRow:
    return models.SubnetRow(
        id=subnet.id,
        network_id=network_id,
        cidr=subnet.cidr,
        gateway=subnet.gateway,
        dns_servers=list(subnet.dns_servers),
        allocation_pools=[_range_to_json(r) for r in subnet.allocation_pools],
        reserved_ranges=[_range_to_json(r) for r in subnet.reserved_ranges],
        dhcp=_dhcp_to_json(subnet.dhcp),
        dns_zone=subnet.dns_zone,
    )


def subnet_from_row(row: models.SubnetRow) -> Subnet:
    return Subnet(
        id=SubnetId(row.id),
        cidr=row.cidr,
        gateway=row.gateway,
        dns_servers=tuple(row.dns_servers or ()),
        allocation_pools=tuple(_range_from_json(r) for r in row.allocation_pools or ()),
        reserved_ranges=tuple(_range_from_json(r) for r in row.reserved_ranges or ()),
        dhcp=_dhcp_from_json(row.dhcp),
        dns_zone=row.dns_zone,
    )


def _range_to_json(rng: IpRange) -> dict[str, str]:
    return {"start": rng.start, "end": rng.end}


def _range_from_json(blob: dict[str, Any]) -> IpRange:
    return IpRange(start=str(blob["start"]), end=str(blob["end"]))


def _dhcp_to_json(dhcp: DhcpSpec | None) -> dict[str, Any] | None:
    if dhcp is None:
        return None
    return {
        "range_start": dhcp.range_start,
        "range_end": dhcp.range_end,
        "lease_time_seconds": dhcp.lease_time_seconds,
        "domain_name": dhcp.domain_name,
    }


def _dhcp_from_json(blob: dict[str, Any] | None) -> DhcpSpec | None:
    if blob is None:
        return None
    return DhcpSpec(
        range_start=str(blob["range_start"]),
        range_end=str(blob["range_end"]),
        lease_time_seconds=int(blob.get("lease_time_seconds", 3600)),
        domain_name=blob.get("domain_name"),
    )


def _nat_to_json(nat: NatSpec | None) -> dict[str, Any] | None:
    if nat is None:
        return None
    return {"egress_interface": nat.egress_interface}


def _nat_from_json(blob: dict[str, Any] | None) -> NatSpec | None:
    if blob is None:
        return None
    return NatSpec(egress_interface=str(blob["egress_interface"]))


def _firewall_to_json(fw: FirewallPolicy | None) -> dict[str, Any] | None:
    if fw is None:
        return None
    return {
        "default_action": fw.default_action.value,
        "rules": [
            {
                "action": r.action.value,
                "proto": r.proto.value,
                "source_cidr": r.source_cidr,
                "destination_cidr": r.destination_cidr,
                "destination_port_start": r.destination_port_start,
                "destination_port_end": r.destination_port_end,
            }
            for r in fw.rules
        ],
    }


def _firewall_from_json(blob: dict[str, Any] | None) -> FirewallPolicy | None:
    if blob is None:
        return None
    return FirewallPolicy(
        default_action=FirewallAction(blob.get("default_action", "drop")),
        rules=tuple(
            FirewallRule(
                action=FirewallAction(r.get("action", "accept")),
                proto=FirewallProto(r.get("proto", "any")),
                source_cidr=r.get("source_cidr"),
                destination_cidr=r.get("destination_cidr"),
                destination_port_start=r.get("destination_port_start"),
                destination_port_end=r.get("destination_port_end"),
            )
            for r in blob.get("rules") or ()
        ),
    )


def network_from_row(row: models.NetworkRow) -> Network:
    subnet: Subnet | None = subnet_from_row(row.subnet) if row.subnet is not None else None
    return Network(
        id=NetworkId(row.id),
        name=row.name,
        type=NetworkType(row.type),
        mtu=row.mtu,
        vlan_id=row.vlan_id,
        vni=row.vni,
        subnet=subnet,
        labels=dict(row.labels),
        intent_version=row.intent_version,
        node_ids=tuple(NodeId(n) for n in row.node_ids),
        nat=_nat_from_json(row.nat),
        firewall_policy=_firewall_from_json(row.firewall_policy),
        spec_hash=row.spec_hash,
        project_id=ProjectId(row.project_id) if row.project_id else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# ObservedState
# ---------------------------------------------------------------------------


def observed_state_to_row(state: ObservedState) -> models.ObservedStateRow:
    return models.ObservedStateRow(
        node_id=state.node_id,
        observed_at=state.observed_at,
        state_hash=state.state_hash,
        payload={
            "bridges": [_observed_bridge_to_json(b) for b in state.bridges],
        },
    )


def observed_state_from_row(row: models.ObservedStateRow) -> ObservedState:
    bridges_raw = (row.payload or {}).get("bridges") or []
    return ObservedState(
        node_id=NodeId(row.node_id),
        observed_at=row.observed_at,
        state_hash=row.state_hash,
        bridges=tuple(_observed_bridge_from_json(b) for b in bridges_raw),
    )


def _observed_bridge_to_json(b: ObservedBridge) -> dict[str, Any]:
    return {
        "name": b.name,
        "datapath_type": b.datapath_type,
        "external_ids": dict(b.external_ids),
        "ports": [_observed_port_to_json(p) for p in b.ports],
    }


def _observed_bridge_from_json(d: dict[str, Any]) -> ObservedBridge:
    return ObservedBridge(
        name=str(d["name"]),
        datapath_type=str(d.get("datapath_type") or "system"),
        external_ids=dict(d.get("external_ids") or {}),
        ports=tuple(_observed_port_from_json(p) for p in d.get("ports") or []),
    )


def _observed_port_to_json(p: ObservedPort) -> dict[str, Any]:
    return {
        "name": p.name,
        "tag": p.tag,
        "trunks": list(p.trunks),
        "external_ids": dict(p.external_ids),
        "interfaces": [
            {"name": i.name, "type": i.type, "options": dict(i.options)} for i in p.interfaces
        ],
    }


def _observed_port_from_json(d: dict[str, Any]) -> ObservedPort:
    interfaces = tuple(
        ObservedInterface(
            name=str(i["name"]),
            type=str(i.get("type") or "internal"),
            options=dict(i.get("options") or {}),
        )
        for i in d.get("interfaces") or []
    )
    return ObservedPort(
        name=str(d["name"]),
        tag=d.get("tag"),
        trunks=tuple(int(t) for t in (d.get("trunks") or ())),
        external_ids=dict(d.get("external_ids") or {}),
        interfaces=interfaces,
    )


# ---------------------------------------------------------------------------
# Operation
# ---------------------------------------------------------------------------


def operation_event_to_row(operation_id: str, evt: OperationEvent) -> models.OperationEventRow:
    return models.OperationEventRow(
        operation_id=operation_id,
        sequence=evt.sequence,
        at=evt.at,
        status=evt.status.value,
        message=evt.message,
        payload=dict(evt.payload),
    )


def operation_event_from_row(row: models.OperationEventRow) -> OperationEvent:
    return OperationEvent(
        sequence=row.sequence,
        at=row.at,
        status=OperationStatus(row.status),
        message=row.message,
        payload=dict(row.payload),
    )


def operation_to_row(op: Operation) -> models.OperationRow:
    row = models.OperationRow(
        id=op.id,
        kind=op.kind.value,
        status=op.status.value,
        resource_type=op.resource.type,
        resource_id=op.resource.id,
        created_at=op.created_at,
        updated_at=op.updated_at,
        created_by=op.created_by,
        error_code=op.error.code if op.error is not None else None,
        error_message=op.error.message if op.error is not None else None,
        error_details=dict(op.error.details) if op.error is not None else None,
    )
    row.events = [operation_event_to_row(op.id, evt) for evt in op.events]
    return row


def operation_from_row(row: models.OperationRow) -> Operation:
    error: OperationError | None = None
    if row.error_code is not None:
        error = OperationError(
            code=row.error_code,
            message=row.error_message or "",
            details=dict(row.error_details or {}),
        )
    return Operation(
        id=OperationId(row.id),
        kind=OperationKind(row.kind),
        status=OperationStatus(row.status),
        resource=ResourceRef(type=row.resource_type, id=row.resource_id),
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by=row.created_by,
        events=[operation_event_from_row(evt) for evt in row.events],
        error=error,
    )


# ---------------------------------------------------------------------------
# IpAllocation
# ---------------------------------------------------------------------------


def ip_allocation_to_row(allocation: IpAllocation) -> models.IpAllocationRow:
    return models.IpAllocationRow(
        id=allocation.id,
        subnet_id=allocation.subnet_id,
        ip_address=allocation.ip_address,
        owner_type=allocation.owner.type,
        owner_id=allocation.owner.id,
        kind=allocation.kind.value,
        allocated_at=allocation.allocated_at,
        label=allocation.label,
    )


def ip_allocation_from_row(row: models.IpAllocationRow) -> IpAllocation:
    return IpAllocation(
        id=IpAllocationId(row.id),
        subnet_id=SubnetId(row.subnet_id),
        ip_address=row.ip_address,
        owner=OwnerRef(type=row.owner_type, id=row.owner_id),
        kind=IpAllocationKind(row.kind),
        allocated_at=row.allocated_at,
        label=row.label,
    )


# ---------------------------------------------------------------------------
# ServiceAccount + ServiceToken
# ---------------------------------------------------------------------------


def service_account_to_row(account: ServiceAccount) -> models.ServiceAccountRow:
    return models.ServiceAccountRow(
        id=account.id,
        name=account.name,
        role=account.role.value,
        description=account.description,
        labels=dict(account.labels),
        created_at=account.created_at,
        updated_at=account.updated_at,
        created_by=account.created_by,
        disabled_at=account.disabled_at,
    )


def service_account_from_row(row: models.ServiceAccountRow) -> ServiceAccount:
    return ServiceAccount(
        id=ServiceAccountId(row.id),
        name=row.name,
        role=Role(row.role),
        description=row.description,
        labels=dict(row.labels),
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by=row.created_by,
        disabled_at=row.disabled_at,
    )


def service_token_to_row(token: ServiceToken) -> models.ServiceTokenRow:
    return models.ServiceTokenRow(
        id=token.id,
        service_account_id=token.service_account_id,
        token_hash=token.token_hash,
        issued_at=token.issued_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        revoked_at=token.revoked_at,
        issued_by=token.issued_by,
        label=token.label,
    )


def service_token_from_row(row: models.ServiceTokenRow) -> ServiceToken:
    return ServiceToken(
        id=ServiceTokenId(row.id),
        service_account_id=ServiceAccountId(row.service_account_id),
        token_hash=row.token_hash,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        issued_by=row.issued_by,
        label=row.label,
    )


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------


def audit_event_to_row(event: AuditEvent) -> models.AuditEventRow:
    return models.AuditEventRow(
        id=event.id,
        at=event.at,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        actor=event.actor,
        http_status=event.http_status,
        request_id=event.request_id,
        payload=dict(event.payload),
    )


def audit_event_from_row(row: models.AuditEventRow) -> AuditEvent:
    return AuditEvent(
        id=AuditEventId(row.id),
        at=row.at,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        actor=row.actor,
        http_status=row.http_status,
        request_id=row.request_id,
        payload=dict(row.payload),
    )


def node_snapshot_to_row(snap: NodeSnapshot) -> models.NodeSnapshotRow:
    return models.NodeSnapshotRow(
        id=snap.id,
        node_id=snap.node_id,
        agent_snapshot_id=snap.agent_snapshot_id,
        state_hash=snap.state_hash,
        created_at=snap.created_at,
        label=snap.label,
    )


def node_snapshot_from_row(row: models.NodeSnapshotRow) -> NodeSnapshot:
    return NodeSnapshot(
        id=NodeSnapshotId(row.id),
        node_id=NodeId(row.node_id),
        agent_snapshot_id=row.agent_snapshot_id,
        state_hash=row.state_hash,
        created_at=row.created_at,
        label=row.label,
    )


def outbox_event_to_row(event: OutboxEvent) -> models.OutboxEventRow:
    # ``event_id`` приходит как 0 для свежего события; SQLAlchemy
    # пропустит его как «незаполненный INTEGER PK», и DB-driver
    # подставит autoincrement. Для уже materialized-события (приём
    # из репы) сохраняем переданный ``event_id``.
    kwargs: dict[str, Any] = {
        "id": event.id,
        "occurred_at": event.occurred_at,
        "event_type": event.event_type,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "payload": dict(event.payload),
        "delivered_at": event.delivered_at,
        "schema_version": event.schema_version,
        "project_id": event.project_id,
    }
    if event.event_id > 0:
        kwargs["event_id"] = event.event_id
    return models.OutboxEventRow(**kwargs)


def outbox_event_from_row(row: models.OutboxEventRow) -> OutboxEvent:
    return OutboxEvent(
        id=OutboxEventId(row.id),
        event_id=row.event_id,
        occurred_at=row.occurred_at,
        event_type=row.event_type,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        payload=dict(row.payload),
        delivered_at=row.delivered_at,
        schema_version=getattr(row, "schema_version", 2),
        project_id=row.project_id,
    )


# ---------------------------------------------------------------------------
# Project / ProjectMember  (N0)
# ---------------------------------------------------------------------------


def project_to_row(project: Project) -> models.ProjectRow:
    return models.ProjectRow(
        id=project.id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        labels=dict(project.labels),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def project_from_row(row: models.ProjectRow) -> Project:
    return Project(
        id=ProjectId(row.id),
        name=row.name,
        slug=row.slug,
        description=row.description,
        labels=dict(row.labels),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def project_member_to_row(member: ProjectMember) -> models.ProjectMemberRow:
    return models.ProjectMemberRow(
        project_id=member.project_id,
        service_account_id=member.service_account_id,
        role=member.role.value,
        created_at=member.created_at,
        created_by=member.created_by,
    )


def project_member_from_row(row: models.ProjectMemberRow) -> ProjectMember:
    return ProjectMember(
        project_id=ProjectId(row.project_id),
        service_account_id=ServiceAccountId(row.service_account_id),
        role=Role(row.role),
        created_at=row.created_at,
        created_by=row.created_by,
    )


def webhook_subscription_to_row(
    sub: WebhookSubscription,
) -> models.WebhookSubscriptionRow:
    return models.WebhookSubscriptionRow(
        id=sub.id,
        target_url=sub.target_url,
        secret_hash=sub.secret_hash,
        event_types=list(sub.event_types),
        state=sub.state.value,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
        cursor=sub.cursor,
        last_delivery_at=sub.last_delivery_at,
        last_delivery_status=sub.last_delivery_status,
        failure_count=sub.failure_count,
        description=sub.description,
        labels=dict(sub.labels),
    )


def webhook_subscription_from_row(
    row: models.WebhookSubscriptionRow,
) -> WebhookSubscription:
    return WebhookSubscription(
        id=WebhookSubscriptionId(row.id),
        target_url=row.target_url,
        secret_hash=row.secret_hash,
        event_types=tuple(row.event_types or ()),
        state=WebhookSubscriptionState(row.state),
        created_at=row.created_at,
        updated_at=row.updated_at,
        cursor=row.cursor,
        last_delivery_at=row.last_delivery_at,
        last_delivery_status=row.last_delivery_status,
        failure_count=row.failure_count,
        description=row.description,
        labels=dict(row.labels or {}),
    )


# ---------------------------------------------------------------------------
# N1 mappers
# ---------------------------------------------------------------------------


def logical_port_to_row(port: LogicalPort) -> models.LogicalPortRow:
    return models.LogicalPortRow(
        id=port.id,
        name=port.name,
        node_id=port.node_id,
        network_id=port.network_id,
        vif_id=port.vif_id,
        mac_address=port.mac_address,
        ip_address=port.ip_address,
        status=port.status.value,
        project_id=port.project_id,
        labels=dict(port.labels),
        created_at=port.created_at,
        updated_at=port.updated_at,
    )


def logical_port_from_row(row: models.LogicalPortRow) -> LogicalPort:
    return LogicalPort(
        id=LogicalPortId(row.id),
        name=row.name,
        node_id=NodeId(row.node_id),
        network_id=NetworkId(row.network_id),
        vif_id=row.vif_id,
        mac_address=row.mac_address,
        ip_address=row.ip_address,
        status=LogicalPortStatus(row.status),
        project_id=ProjectId(row.project_id) if row.project_id else None,
        labels=dict(row.labels or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def security_group_to_row(sg: SecurityGroup) -> models.SecurityGroupRow:
    return models.SecurityGroupRow(
        id=sg.id,
        name=sg.name,
        description=sg.description,
        project_id=sg.project_id,
        labels=dict(sg.labels),
        created_at=sg.created_at,
        updated_at=sg.updated_at,
    )


def security_group_from_row(row: models.SecurityGroupRow) -> SecurityGroup:
    return SecurityGroup(
        id=SecurityGroupId(row.id),
        name=row.name,
        description=row.description or "",
        project_id=ProjectId(row.project_id) if row.project_id else None,
        labels=dict(row.labels or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def sg_member_to_row(m: SecurityGroupMember) -> models.SecurityGroupMemberRow:
    return models.SecurityGroupMemberRow(
        sg_id=m.sg_id,
        member_type=m.member_type,
        member_value=m.member_value,
        created_at=m.created_at,
    )


def sg_member_from_row(row: models.SecurityGroupMemberRow) -> SecurityGroupMember:
    return SecurityGroupMember(
        sg_id=SecurityGroupId(row.sg_id),
        member_type=row.member_type,
        member_value=row.member_value,
        created_at=row.created_at,
    )


def address_pool_to_row(pool: AddressPool) -> models.AddressPoolRow:
    return models.AddressPoolRow(
        id=pool.id,
        name=pool.name,
        description=pool.description,
        project_id=pool.project_id,
        cidrs=list(pool.cidrs),
        labels=dict(pool.labels),
        created_at=pool.created_at,
        updated_at=pool.updated_at,
    )


def address_pool_from_row(row: models.AddressPoolRow) -> AddressPool:
    return AddressPool(
        id=AddressPoolId(row.id),
        name=row.name,
        description=row.description or "",
        project_id=ProjectId(row.project_id) if row.project_id else None,
        cidrs=tuple(row.cidrs or ()),
        labels=dict(row.labels or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def service_object_to_row(obj: ServiceObject) -> models.ServiceObjectRow:
    return models.ServiceObjectRow(
        id=obj.id,
        name=obj.name,
        description=obj.description,
        project_id=obj.project_id,
        protocol=obj.protocol,
        ports=list(obj.ports),
        labels=dict(obj.labels),
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


def service_object_from_row(row: models.ServiceObjectRow) -> ServiceObject:
    return ServiceObject(
        id=ServiceObjectId(row.id),
        name=row.name,
        description=row.description or "",
        project_id=ProjectId(row.project_id) if row.project_id else None,
        protocol=row.protocol,
        ports=tuple(row.ports or ()),
        labels=dict(row.labels or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def qos_policy_to_row(policy: QosPolicy) -> models.QosPolicyRow:
    return models.QosPolicyRow(
        id=policy.id,
        name=policy.name,
        description=policy.description,
        project_id=policy.project_id,
        ingress_kbps=policy.ingress_kbps,
        egress_kbps=policy.egress_kbps,
        burst_kb=policy.burst_kb,
        dscp=policy.dscp,
        labels=dict(policy.labels),
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def qos_policy_from_row(row: models.QosPolicyRow) -> QosPolicy:
    return QosPolicy(
        id=QosPolicyId(row.id),
        name=row.name,
        description=row.description or "",
        project_id=ProjectId(row.project_id) if row.project_id else None,
        ingress_kbps=row.ingress_kbps,
        egress_kbps=row.egress_kbps,
        burst_kb=row.burst_kb,
        dscp=row.dscp,
        labels=dict(row.labels or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# N2 — SecurityPolicy
# ---------------------------------------------------------------------------


def security_policy_rule_to_row(
    rule: SecurityPolicyRule,
    policy_id: str,
) -> models.SecurityPolicyRuleRow:
    return models.SecurityPolicyRuleRow(
        policy_id=policy_id,
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


def security_policy_rule_from_row(row: models.SecurityPolicyRuleRow) -> SecurityPolicyRule:
    from sdn_controller.core.value_objects.ids import ServiceObjectId as SvcObjId
    return SecurityPolicyRule(
        rule_id=row.rule_id,
        priority=row.priority,
        direction=row.direction,
        action=row.action,
        source_type=row.source_type or "any",
        source_value=row.source_value or "",
        destination_type=row.destination_type or "any",
        destination_value=row.destination_value or "",
        service_object_id=SvcObjId(row.service_object_id) if row.service_object_id else None,
        enabled=row.enabled if row.enabled is not None else True,
        comment=row.comment or "",
        packet_count=row.packet_count or 0,
        byte_count=row.byte_count or 0,
    )


def security_policy_to_row(policy: SecurityPolicy) -> models.SecurityPolicyRow:
    row = models.SecurityPolicyRow(
        id=policy.id,
        name=policy.name,
        description=policy.description,
        project_id=policy.project_id,
        labels=dict(policy.labels),
        status=str(policy.status),
        compiled_ruleset=policy.compiled_ruleset,
        compiled_at=policy.compiled_at,
        applied_at=policy.applied_at,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
        rules=[security_policy_rule_to_row(r, str(policy.id)) for r in policy.rules],
    )
    return row


def security_policy_from_row(row: models.SecurityPolicyRow) -> SecurityPolicy:
    rules = tuple(
        security_policy_rule_from_row(r)
        for r in sorted(row.rules, key=lambda r: r.priority)
    )
    return SecurityPolicy(
        id=SecurityPolicyId(row.id),
        name=row.name,
        description=row.description or "",
        project_id=ProjectId(row.project_id) if row.project_id else None,
        labels=dict(row.labels or {}),
        rules=rules,
        status=SecurityPolicyStatus(row.status),
        compiled_ruleset=row.compiled_ruleset,
        compiled_at=row.compiled_at,
        applied_at=row.applied_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# N2 — TrunkPort
# ---------------------------------------------------------------------------


def trunk_port_to_row(port: TrunkPort) -> models.TrunkPortRow:
    return models.TrunkPortRow(
        id=port.id,
        name=port.name,
        node_id=port.node_id,
        logical_port_id=str(port.logical_port_id) if port.logical_port_id else None,
        vlan_ids=list(port.vlan_ids),
        native_vlan=port.native_vlan,
        project_id=port.project_id,
        labels=dict(port.labels),
        created_at=port.created_at,
        updated_at=port.updated_at,
    )


def trunk_port_from_row(row: models.TrunkPortRow) -> TrunkPort:
    from sdn_controller.core.value_objects.ids import LogicalPortId as LPId
    return TrunkPort(
        id=TrunkPortId(row.id),
        name=row.name,
        node_id=NodeId(row.node_id),
        logical_port_id=LPId(row.logical_port_id) if row.logical_port_id else None,
        vlan_ids=tuple(sorted(row.vlan_ids or [])),
        native_vlan=row.native_vlan,
        project_id=ProjectId(row.project_id) if row.project_id else None,
        labels=dict(row.labels or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
