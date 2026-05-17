"""Agent enrolment + heartbeat use cases (SDN-006 / SDN-007).

Three flows:

* ``IssueEnrollmentToken`` — operator issues a one-shot bearer credential for
  an existing ``pending`` node. Plaintext is returned exactly once; only the
  SHA-256 hash is persisted.
* ``EnrollAgent`` — agent presents the plaintext, becomes the node's online
  identity, and reports capabilities. The token is marked consumed.
* ``RecordHeartbeat`` — periodic freshness update. Heartbeating is a recovery
  path for ``stale``/``offline`` nodes; it never auto-enrols a ``pending``
  one (that requires the token).

mTLS / signed agent identity arrive in M9; today the proof of agent identity
is the unspent token at enrolment and (weakly) the node id afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sdn_controller.core.entities import EnrollmentToken, Node, hash_token
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.enums import NodeStatus
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import IdFactory, NodeId
from sdn_controller.ports.persistence import (
    EnrollmentTokenRepository,
    NodeRepository,
)
from sdn_controller.ports.security import TokenFactory

# ---------------------------------------------------------------------------
# Commands / results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IssuedEnrollmentToken:
    """Bundle returned to the operator at issue time.

    ``plaintext`` is the only place the raw token is ever exposed — clients
    must capture it from this response. The persisted row stores only the
    hash, so a leaked DB snapshot does not leak usable tokens.
    """

    token: EnrollmentToken
    plaintext: str


@dataclass(frozen=True, slots=True)
class EnrollAgentCommand:
    plaintext: str
    agent_version: str | None = None
    capabilities: NodeCapabilities | None = None


@dataclass(frozen=True, slots=True)
class HeartbeatCommand:
    node_id: NodeId
    agent_version: str | None = None
    capabilities: NodeCapabilities | None = None


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


class IssueEnrollmentToken:
    def __init__(
        self,
        *,
        nodes: NodeRepository,
        tokens: EnrollmentTokenRepository,
        clock: Clock,
        ids: IdFactory,
        token_factory: TokenFactory,
        ttl_seconds: int,
    ) -> None:
        self._nodes = nodes
        self._tokens = tokens
        self._clock = clock
        self._ids = ids
        self._token_factory = token_factory
        self._ttl_seconds = ttl_seconds

    async def execute(
        self, node_id: NodeId, *, issued_by: str | None = None
    ) -> IssuedEnrollmentToken:
        node = await self._nodes.get(node_id)
        if node is None:
            raise NotFoundError(f"node {node_id} not found")
        if node.status is not NodeStatus.PENDING:
            raise ConflictError(
                f"node {node_id} is not pending (status={node.status.value}); "
                "tokens can only be issued for pending nodes",
            )

        plaintext = self._token_factory.enrollment_token_plaintext()
        token = EnrollmentToken.issue(
            token_id=self._ids.enrollment_token(),
            node_id=node_id,
            plaintext=plaintext,
            now=self._clock.now(),
            ttl=timedelta(seconds=self._ttl_seconds),
            issued_by=issued_by,
        )
        await self._tokens.save(token)
        return IssuedEnrollmentToken(token=token, plaintext=plaintext)


class EnrollAgent:
    """Consume a token and transition the bound node to ``online``."""

    def __init__(
        self,
        *,
        nodes: NodeRepository,
        tokens: EnrollmentTokenRepository,
        clock: Clock,
    ) -> None:
        self._nodes = nodes
        self._tokens = tokens
        self._clock = clock

    async def execute(self, cmd: EnrollAgentCommand) -> Node:
        if not cmd.plaintext or not cmd.plaintext.strip():
            raise ValidationError("plaintext token must be provided")

        token = await self._tokens.get_by_hash(hash_token(cmd.plaintext))
        # Same response shape for unknown / expired / used tokens — don't help
        # an attacker distinguish failure modes.
        if token is None:
            raise NotFoundError("enrollment token is invalid")

        now = self._clock.now()
        # ``consume`` enforces single-use + expiry; raises ConflictError otherwise.
        token.consume(now=now)

        node = await self._nodes.get(token.node_id)
        if node is None:
            # Token outlived its node — clean up the orphan and refuse.
            await self._tokens.delete_for_node(token.node_id)
            raise NotFoundError("enrollment token is invalid")
        node.enroll(
            now=now,
            agent_version=cmd.agent_version,
            capabilities=cmd.capabilities,
        )

        await self._tokens.save(token)
        await self._nodes.save(node)
        return node


class RecordHeartbeat:
    """Update freshness for an enrolled node."""

    def __init__(self, *, nodes: NodeRepository, clock: Clock) -> None:
        self._nodes = nodes
        self._clock = clock

    async def execute(self, cmd: HeartbeatCommand) -> Node:
        node = await self._nodes.get(cmd.node_id)
        if node is None:
            raise NotFoundError(f"node {cmd.node_id} not found")
        node.record_heartbeat(
            now=self._clock.now(),
            agent_version=cmd.agent_version,
            capabilities=cmd.capabilities,
        )
        await self._nodes.save(node)
        return node
