"""Mapping functions between SQL rows and domain aggregates.

Keeping the conversion in one place means the ORM models stay an
adapter-internal detail — repositories accept and return pure dataclasses,
exactly like the in-memory adapter does.
"""

from __future__ import annotations

from typing import Any

from sdn_controller.adapters.sql import models
from sdn_controller.core.entities import (
    EnrollmentToken,
    Network,
    Node,
    Operation,
    OperationError,
    OperationEvent,
    ResourceRef,
    Subnet,
)
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.enums import (
    NetworkType,
    NodeStatus,
    OperationKind,
    OperationStatus,
)
from sdn_controller.core.value_objects.ids import (
    EnrollmentTokenId,
    NetworkId,
    NodeId,
    OperationId,
    SubnetId,
)


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
        created_at=network.created_at,
        updated_at=network.updated_at,
    )
    if network.subnet is not None:
        row.subnet = models.SubnetRow(
            id=network.subnet.id,
            network_id=network.id,
            cidr=network.subnet.cidr,
            gateway=network.subnet.gateway,
        )
    return row


def network_from_row(row: models.NetworkRow) -> Network:
    subnet: Subnet | None = None
    if row.subnet is not None:
        subnet = Subnet(
            id=SubnetId(row.subnet.id),
            cidr=row.subnet.cidr,
            gateway=row.subnet.gateway,
        )
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
        created_at=row.created_at,
        updated_at=row.updated_at,
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
