"""Typed identifiers and factories.

We use a Stripe-style prefixed identifier (``prefix_<random>``) so logs and APIs
remain self-describing even when ids leak across resource boundaries. The
underlying randomness comes from UUIDv4 today; we can swap to ULID/UUIDv7 later
without changing the public type.
"""

from __future__ import annotations

import uuid
from typing import NewType, Protocol

# ---------------------------------------------------------------------------
# Typed id aliases
# ---------------------------------------------------------------------------
# NewType gives us nominal typing in mypy ("an OperationId is not a str") without
# any runtime overhead. Each id type has a stable textual prefix.

NodeId = NewType("NodeId", str)
NetworkId = NewType("NetworkId", str)
SubnetId = NewType("SubnetId", str)
OperationId = NewType("OperationId", str)
EnrollmentTokenId = NewType("EnrollmentTokenId", str)
IpAllocationId = NewType("IpAllocationId", str)
ServiceAccountId = NewType("ServiceAccountId", str)
ServiceTokenId = NewType("ServiceTokenId", str)
AuditEventId = NewType("AuditEventId", str)
NodeSnapshotId = NewType("NodeSnapshotId", str)
OutboxEventId = NewType("OutboxEventId", str)
WebhookSubscriptionId = NewType("WebhookSubscriptionId", str)
# N0 — multitenancy
ProjectId = NewType("ProjectId", str)
# N1 — LogicalPort + Security Operands
LogicalPortId = NewType("LogicalPortId", str)
SecurityGroupId = NewType("SecurityGroupId", str)
AddressPoolId = NewType("AddressPoolId", str)
ServiceObjectId = NewType("ServiceObjectId", str)
QosPolicyId = NewType("QosPolicyId", str)
# N2 — SecurityPolicy + TrunkPort
SecurityPolicyId = NewType("SecurityPolicyId", str)
TrunkPortId = NewType("TrunkPortId", str)
# N3 — Router / FloatingIP / BGP
RouterId = NewType("RouterId", str)
FloatingIpId = NewType("FloatingIpId", str)
BgpPeerId = NewType("BgpPeerId", str)
# N4 — Governance & Scale
ProjectQuotaId = NewType("ProjectQuotaId", str)
ResourceSnapshotId = NewType("ResourceSnapshotId", str)
RetentionPolicyId = NewType("RetentionPolicyId", str)
GatewayBondId = NewType("GatewayBondId", str)
LoadBalancerId = NewType("LoadBalancerId", str)
LbPoolId = NewType("LbPoolId", str)
LbMemberId = NewType("LbMemberId", str)
LbListenerId = NewType("LbListenerId", str)
HealthMonitorId = NewType("HealthMonitorId", str)

_PREFIXES: dict[str, str] = {
    "NodeId": "node",
    "NetworkId": "net",
    "SubnetId": "sub",
    "OperationId": "op",
    "EnrollmentTokenId": "enroll",
    "IpAllocationId": "ipa",
    "ServiceAccountId": "sa",
    "ServiceTokenId": "tok",
    "AuditEventId": "audit",
    "NodeSnapshotId": "snap",
    "OutboxEventId": "outbox",
    "WebhookSubscriptionId": "whsub",
    "ProjectId": "proj",
    # N1
    "LogicalPortId": "lport",
    "SecurityGroupId": "sg",
    "AddressPoolId": "apool",
    "ServiceObjectId": "svcobj",
    "QosPolicyId": "qos",
    # N2
    "SecurityPolicyId": "spol",
    "TrunkPortId": "tport",
    # N3
    "RouterId": "rtr",
    "FloatingIpId": "fip",
    "BgpPeerId": "bgpp",
    # N4
    "ProjectQuotaId": "pquota",
    "ResourceSnapshotId": "rsnap",
    "RetentionPolicyId": "ret",
    "GatewayBondId": "gbond",
    "LoadBalancerId": "lb",
    "LbPoolId": "lbpool",
    "LbMemberId": "lbm",
    "LbListenerId": "lblis",
    "HealthMonitorId": "hm",
}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Factory port
# ---------------------------------------------------------------------------


class IdFactory(Protocol):
    """Generates new ids. Use cases depend on this port instead of ``uuid``.

    Tests can substitute a deterministic factory so assertions don't have to
    match arbitrary random strings.
    """

    def node(self) -> NodeId: ...
    def network(self) -> NetworkId: ...
    def subnet(self) -> SubnetId: ...
    def operation(self) -> OperationId: ...
    def enrollment_token(self) -> EnrollmentTokenId: ...
    def ip_allocation(self) -> IpAllocationId: ...
    def service_account(self) -> ServiceAccountId: ...
    def service_token(self) -> ServiceTokenId: ...
    def audit_event(self) -> AuditEventId: ...
    def node_snapshot(self) -> NodeSnapshotId: ...
    def outbox_event(self) -> OutboxEventId: ...
    def webhook_subscription(self) -> WebhookSubscriptionId: ...
    def project(self) -> ProjectId: ...
    # N1
    def logical_port(self) -> LogicalPortId: ...
    def security_group(self) -> SecurityGroupId: ...
    def address_pool(self) -> AddressPoolId: ...
    def service_object(self) -> ServiceObjectId: ...
    def qos_policy(self) -> QosPolicyId: ...
    # N2
    def security_policy(self) -> SecurityPolicyId: ...
    def trunk_port(self) -> TrunkPortId: ...
    # N3
    def router(self) -> RouterId: ...
    def floating_ip(self) -> FloatingIpId: ...
    def bgp_peer(self) -> BgpPeerId: ...
    # N4
    def project_quota(self) -> ProjectQuotaId: ...
    def resource_snapshot(self) -> ResourceSnapshotId: ...
    def retention_policy(self) -> RetentionPolicyId: ...
    def gateway_bond(self) -> GatewayBondId: ...
    def load_balancer(self) -> LoadBalancerId: ...
    def lb_pool(self) -> LbPoolId: ...
    def lb_member(self) -> LbMemberId: ...
    def lb_listener(self) -> LbListenerId: ...
    def health_monitor(self) -> HealthMonitorId: ...


class UuidIdFactory:
    """Default production factory backed by UUIDv4."""

    def node(self) -> NodeId:
        return NodeId(_new_id(_PREFIXES["NodeId"]))

    def network(self) -> NetworkId:
        return NetworkId(_new_id(_PREFIXES["NetworkId"]))

    def subnet(self) -> SubnetId:
        return SubnetId(_new_id(_PREFIXES["SubnetId"]))

    def operation(self) -> OperationId:
        return OperationId(_new_id(_PREFIXES["OperationId"]))

    def enrollment_token(self) -> EnrollmentTokenId:
        return EnrollmentTokenId(_new_id(_PREFIXES["EnrollmentTokenId"]))

    def ip_allocation(self) -> IpAllocationId:
        return IpAllocationId(_new_id(_PREFIXES["IpAllocationId"]))

    def service_account(self) -> ServiceAccountId:
        return ServiceAccountId(_new_id(_PREFIXES["ServiceAccountId"]))

    def service_token(self) -> ServiceTokenId:
        return ServiceTokenId(_new_id(_PREFIXES["ServiceTokenId"]))

    def audit_event(self) -> AuditEventId:
        return AuditEventId(_new_id(_PREFIXES["AuditEventId"]))

    def node_snapshot(self) -> NodeSnapshotId:
        return NodeSnapshotId(_new_id(_PREFIXES["NodeSnapshotId"]))

    def outbox_event(self) -> OutboxEventId:
        return OutboxEventId(_new_id(_PREFIXES["OutboxEventId"]))

    def webhook_subscription(self) -> WebhookSubscriptionId:
        return WebhookSubscriptionId(_new_id(_PREFIXES["WebhookSubscriptionId"]))

    def project(self) -> ProjectId:
        return ProjectId(_new_id(_PREFIXES["ProjectId"]))

    # N1
    def logical_port(self) -> LogicalPortId:
        return LogicalPortId(_new_id(_PREFIXES["LogicalPortId"]))

    def security_group(self) -> SecurityGroupId:
        return SecurityGroupId(_new_id(_PREFIXES["SecurityGroupId"]))

    def address_pool(self) -> AddressPoolId:
        return AddressPoolId(_new_id(_PREFIXES["AddressPoolId"]))

    def service_object(self) -> ServiceObjectId:
        return ServiceObjectId(_new_id(_PREFIXES["ServiceObjectId"]))

    def qos_policy(self) -> QosPolicyId:
        return QosPolicyId(_new_id(_PREFIXES["QosPolicyId"]))

    # N2
    def security_policy(self) -> SecurityPolicyId:
        return SecurityPolicyId(_new_id(_PREFIXES["SecurityPolicyId"]))

    def trunk_port(self) -> TrunkPortId:
        return TrunkPortId(_new_id(_PREFIXES["TrunkPortId"]))

    # N3
    def router(self) -> RouterId:
        return RouterId(_new_id(_PREFIXES["RouterId"]))

    def floating_ip(self) -> FloatingIpId:
        return FloatingIpId(_new_id(_PREFIXES["FloatingIpId"]))

    def bgp_peer(self) -> BgpPeerId:
        return BgpPeerId(_new_id(_PREFIXES["BgpPeerId"]))

    # N4
    def project_quota(self) -> ProjectQuotaId:
        return ProjectQuotaId(_new_id(_PREFIXES["ProjectQuotaId"]))

    def resource_snapshot(self) -> ResourceSnapshotId:
        return ResourceSnapshotId(_new_id(_PREFIXES["ResourceSnapshotId"]))

    def retention_policy(self) -> RetentionPolicyId:
        return RetentionPolicyId(_new_id(_PREFIXES["RetentionPolicyId"]))

    def gateway_bond(self) -> GatewayBondId:
        return GatewayBondId(_new_id(_PREFIXES["GatewayBondId"]))

    def load_balancer(self) -> LoadBalancerId:
        return LoadBalancerId(_new_id(_PREFIXES["LoadBalancerId"]))

    def lb_pool(self) -> LbPoolId:
        return LbPoolId(_new_id(_PREFIXES["LbPoolId"]))

    def lb_member(self) -> LbMemberId:
        return LbMemberId(_new_id(_PREFIXES["LbMemberId"]))

    def lb_listener(self) -> LbListenerId:
        return LbListenerId(_new_id(_PREFIXES["LbListenerId"]))

    def health_monitor(self) -> HealthMonitorId:
        return HealthMonitorId(_new_id(_PREFIXES["HealthMonitorId"]))
