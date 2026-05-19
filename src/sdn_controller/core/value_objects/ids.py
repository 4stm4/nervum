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
