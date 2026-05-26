"""SQLAlchemy persistence adapter.

The same module backs both SQLite (MVP default) and PostgreSQL (future): only
the database URL — and, optionally, engine-level args — differ. Repository
classes implement the same Protocols as the in-memory adapter so use cases are
unaware of the swap.
"""

from sdn_controller.adapters.sql.engine import build_engine, build_sessionmaker
from sdn_controller.adapters.sql.models import Base
from sdn_controller.adapters.sql.repositories import (
    SqlAddressPoolRepository,
    SqlAuditEventRepository,
    SqlEnrollmentTokenRepository,
    SqlIpAllocationRepository,
    SqlLogicalPortRepository,
    SqlNetworkRepository,
    SqlNodeRepository,
    SqlNodeSnapshotRepository,
    SqlObservedStateRepository,
    SqlOperationRepository,
    SqlOutboxRepository,
    SqlProjectMemberRepository,
    SqlProjectRepository,
    SqlQosPolicyRepository,
    SqlSecurityGroupMemberRepository,
    SqlSecurityGroupRepository,
    SqlServiceAccountRepository,
    SqlServiceObjectRepository,
    SqlServiceTokenRepository,
    SqlWebhookSubscriptionRepository,
)

__all__ = [
    "Base",
    "SqlAddressPoolRepository",
    "SqlAuditEventRepository",
    "SqlEnrollmentTokenRepository",
    "SqlIpAllocationRepository",
    "SqlLogicalPortRepository",
    "SqlNetworkRepository",
    "SqlNodeRepository",
    "SqlNodeSnapshotRepository",
    "SqlObservedStateRepository",
    "SqlOperationRepository",
    "SqlOutboxRepository",
    "SqlProjectMemberRepository",
    "SqlProjectRepository",
    "SqlQosPolicyRepository",
    "SqlSecurityGroupMemberRepository",
    "SqlSecurityGroupRepository",
    "SqlServiceAccountRepository",
    "SqlServiceObjectRepository",
    "SqlServiceTokenRepository",
    "SqlWebhookSubscriptionRepository",
    "build_engine",
    "build_sessionmaker",
]
