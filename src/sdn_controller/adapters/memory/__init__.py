"""In-memory adapters.

Used for unit tests, the bootable MVP and local development. Storage is a plain
dict guarded by an ``anyio`` lock so concurrent FastAPI handlers can't race.
"""

from sdn_controller.adapters.memory.repositories import (
    InMemoryAuditEventRepository,
    InMemoryEnrollmentTokenRepository,
    InMemoryIpAllocationRepository,
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryNodeSnapshotRepository,
    InMemoryObservedStateRepository,
    InMemoryOperationRepository,
    InMemoryOutboxRepository,
    InMemoryServiceAccountRepository,
    InMemoryServiceTokenRepository,
    InMemoryWebhookSubscriptionRepository,
)

__all__ = [
    "InMemoryAuditEventRepository",
    "InMemoryEnrollmentTokenRepository",
    "InMemoryIpAllocationRepository",
    "InMemoryNetworkRepository",
    "InMemoryNodeRepository",
    "InMemoryNodeSnapshotRepository",
    "InMemoryObservedStateRepository",
    "InMemoryOperationRepository",
    "InMemoryOutboxRepository",
    "InMemoryServiceAccountRepository",
    "InMemoryServiceTokenRepository",
    "InMemoryWebhookSubscriptionRepository",
]
