"""``EnrollmentToken`` invariants and lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sdn_controller.core.entities import EnrollmentToken
from sdn_controller.core.value_objects.errors import ConflictError, ValidationError
from sdn_controller.core.value_objects.ids import EnrollmentTokenId, NodeId

_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def _issue(*, ttl: timedelta = timedelta(hours=1)) -> EnrollmentToken:
    return EnrollmentToken.issue(
        token_id=EnrollmentTokenId("enroll_1"),
        node_id=NodeId("node_1"),
        plaintext="enroll_secret",
        now=_NOW,
        ttl=ttl,
    )


def test_issue_stores_hash_not_plaintext() -> None:
    token = _issue()

    # 32-byte SHA-256 → 64 hex chars
    assert len(token.token_hash) == 64
    assert "enroll_secret" not in token.token_hash


def test_consume_marks_used_at() -> None:
    token = _issue()

    token.consume(now=_NOW + timedelta(seconds=1))

    assert token.used_at == _NOW + timedelta(seconds=1)
    assert token.is_used() is True


def test_consume_twice_raises_conflict() -> None:
    token = _issue()
    token.consume(now=_NOW + timedelta(seconds=1))

    with pytest.raises(ConflictError, match="already been used"):
        token.consume(now=_NOW + timedelta(seconds=2))


def test_expired_token_cannot_be_consumed() -> None:
    token = _issue(ttl=timedelta(seconds=30))

    with pytest.raises(ConflictError, match="expired"):
        token.consume(now=_NOW + timedelta(seconds=31))


def test_negative_ttl_rejected() -> None:
    with pytest.raises(ValidationError, match="ttl must be positive"):
        EnrollmentToken.issue(
            token_id=EnrollmentTokenId("enroll_1"),
            node_id=NodeId("node_1"),
            plaintext="x",
            now=_NOW,
            ttl=timedelta(seconds=0),
        )


def test_is_expired_threshold_inclusive() -> None:
    token = _issue(ttl=timedelta(seconds=10))

    assert token.is_expired(now=_NOW + timedelta(seconds=9)) is False
    assert token.is_expired(now=_NOW + timedelta(seconds=10)) is True
