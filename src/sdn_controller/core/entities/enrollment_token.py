"""``EnrollmentToken`` — one-shot bearer credential for node enrolment.

Lifecycle:

1. Operator issues a token bound to a *pending* node. Plaintext is shown
   exactly once in the API response; only the SHA-256 hash is persisted.
2. Agent presents the plaintext to ``POST /api/v1/agent/enroll``. The
   controller hashes it, looks up the row, verifies ``expires_at`` and that
   ``used_at`` is unset, then marks it consumed inside the same transaction.

The token never serves as a long-lived credential — once an agent enrolls,
heartbeats authenticate by node id (M9 replaces this with mTLS).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sdn_controller.core.value_objects.errors import (
    ConflictError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import EnrollmentTokenId, NodeId

_PLAINTEXT_PREFIX = "enroll"
_PLAINTEXT_BYTES = 32  # → ~43 chars of base64url, ≈ 256 bits of entropy


def generate_token_plaintext() -> str:
    """Build a fresh, self-describing token string."""
    return f"{_PLAINTEXT_PREFIX}_{secrets.token_urlsafe(_PLAINTEXT_BYTES)}"


def hash_token(plaintext: str) -> str:
    """One-way digest used for storage and lookup."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class EnrollmentToken:
    id: EnrollmentTokenId
    node_id: NodeId
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
    issued_by: str | None = None

    # -- factory -----------------------------------------------------------

    @classmethod
    def issue(
        cls,
        *,
        token_id: EnrollmentTokenId,
        node_id: NodeId,
        plaintext: str,
        now: datetime,
        ttl: timedelta,
        issued_by: str | None = None,
    ) -> EnrollmentToken:
        if ttl <= timedelta(0):
            raise ValidationError("enrollment token ttl must be positive")
        return cls(
            id=token_id,
            node_id=node_id,
            token_hash=hash_token(plaintext),
            issued_at=now,
            expires_at=now + ttl,
            used_at=None,
            issued_by=issued_by,
        )

    # -- queries -----------------------------------------------------------

    def is_expired(self, *, now: datetime) -> bool:
        return now >= self.expires_at

    def is_used(self) -> bool:
        return self.used_at is not None

    # -- behaviour ---------------------------------------------------------

    def consume(self, *, now: datetime) -> None:
        """Mark the token as redeemed. Raises if it cannot be used."""
        if self.used_at is not None:
            raise ConflictError(f"enrollment token {self.id} has already been used")
        if self.is_expired(now=now):
            raise ConflictError(f"enrollment token {self.id} has expired")
        self.used_at = now
