"""In-memory адаптеры.

Используются для unit-тестов, MVP и локальной разработки. Хранилище —
plain dict под anyio-блокировкой для потокобезопасности.
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
    InMemorySecurityPolicyRepository,
    InMemoryServiceAccountRepository,
    InMemoryServiceObjectRepository,
    InMemoryServiceTokenRepository,
    InMemoryTrunkPortRepository,
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
    "InMemorySecurityPolicyRepository",
    "InMemoryServiceAccountRepository",
    "InMemoryServiceObjectRepository",
    "InMemoryServiceTokenRepository",
    "InMemoryTrunkPortRepository",
    "InMemoryWebhookSubscriptionRepository",
]
