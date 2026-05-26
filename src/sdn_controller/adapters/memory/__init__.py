"""In-memory adapters.

Used for unit tests, the bootable MVP and local development. Storage is a plain
dict guarded by an ``anyio`` lock so concurrent FastAPI handlers can't race.
"""

from sdn_controller.adapters.memory.repositories import (
    InMemoryAddressPoolRepository,
    InMemoryAuditEventRepository,
    InMemoryEnrollmentTokenRepository,
    InMemoryIpAllocationRepository,
    InMemoryLogicalPortRepository,
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryNodeSnapshotRepository,
    InMemoryObservedStateRepository,
    InMemoryOperationRepository,
    InMemoryOutboxRepository,
    InMemoryProjectMemberRepository,
    InMemoryProjectRepository,
    InMemoryQosPolicyRepository,
    InMemorySecurityGroupMemberRepository,
    InMemorySecurityGroupRepository,
    InMemoryServiceAccountRepository,
    InMemoryServiceObjectRepository,
    InMemoryServiceTokenRepository,
    InMemoryWebhookSubscriptionRepository,
)

__all__ = [
    "InMemoryAddressPoolRepository",
    "InMemoryAuditEventRepository",
    "InMemoryEnrollmentTokenRepository",
    "InMemoryIpAllocationRepository",
    "InMemoryLogicalPortRepository",
    "InMemoryNetworkRepository",
    "InMemoryNodeRepository",
    "InMemoryNodeSnapshotRepository",
    "InMemoryObservedStateRepository",
    "InMemoryOperationRepository",
    "InMemoryOutboxRepository",
    "InMemoryProjectMemberRepository",
    "InMemoryProjectRepository",
    "InMemoryQosPolicyRepository",
    "InMemorySecurityGroupMemberRepository",
    "InMemorySecurityGroupRepository",
    "InMemoryServiceAccountRepository",
    "InMemoryServiceObjectRepository",
    "InMemoryServiceTokenRepository",
    "InMemoryWebhookSubscriptionRepository",
]
