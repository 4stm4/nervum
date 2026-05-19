"""IssueEnrollmentToken / EnrollAgent / RecordHeartbeat use cases."""

from __future__ import annotations

from datetime import timedelta

import pytest

from sdn_controller.adapters.memory import (
    InMemoryEnrollmentTokenRepository,
    InMemoryNodeRepository,
    InMemoryOperationRepository,
)
from sdn_controller.core.entities import hash_token
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.use_cases.enrollment import (
    EnrollAgent,
    EnrollAgentCommand,
    HeartbeatCommand,
    IssueEnrollmentToken,
    RecordHeartbeat,
)
from sdn_controller.core.use_cases.nodes import RegisterNode, RegisterNodeCommand
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.enums import NodeStatus
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import NodeId
from tests.conftest import CountingIdFactory, FrozenClock, SequentialTokenFactory

_TTL_SECONDS = 3600


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


@pytest.fixture
def repos() -> tuple[
    InMemoryNodeRepository,
    InMemoryEnrollmentTokenRepository,
    InMemoryOperationRepository,
]:
    return (
        InMemoryNodeRepository(),
        InMemoryEnrollmentTokenRepository(),
        InMemoryOperationRepository(),
    )


async def _register(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
    ids: CountingIdFactory,
    events: EventPublisher,
    *,
    name: str = "edge-1",
) -> str:
    nodes, _, operations = repos
    register = RegisterNode(nodes=nodes, operations=operations, clock=clock, ids=ids, events=events)
    result = await register.execute(RegisterNodeCommand(name=name, mgmt_ip="10.0.0.10"))
    return result.node.id


# ---------------------------------------------------------------------------
# IssueEnrollmentToken
# ---------------------------------------------------------------------------


async def test_issue_token_persists_hash_and_returns_plaintext(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    events: EventPublisher,
) -> None:
    nodes, tokens, _ = repos
    node_id = await _register(repos, clock, ids, events)
    issue = IssueEnrollmentToken(
        nodes=nodes,
        tokens=tokens,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        ttl_seconds=_TTL_SECONDS,
    )

    issued = await issue.execute(NodeId(node_id), issued_by="alice")

    assert issued.plaintext == "test-token-1"
    persisted = await tokens.get(issued.token.id)
    assert persisted is not None
    assert persisted.token_hash == hash_token("test-token-1")
    assert persisted.issued_by == "alice"
    assert persisted.expires_at - persisted.issued_at == timedelta(seconds=_TTL_SECONDS)


async def test_issue_token_refuses_non_pending_node(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    events: EventPublisher,
) -> None:
    nodes, tokens, _ = repos
    node_id = await _register(repos, clock, ids, events)
    # Manually advance status to online
    node = await nodes.get(NodeId(node_id))
    assert node is not None
    node.status = NodeStatus.ONLINE
    await nodes.save(node)

    issue = IssueEnrollmentToken(
        nodes=nodes,
        tokens=tokens,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        ttl_seconds=_TTL_SECONDS,
    )

    with pytest.raises(ConflictError, match="not pending"):
        await issue.execute(NodeId(node_id))


async def test_issue_token_unknown_node(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    events: EventPublisher,
) -> None:
    nodes, tokens, _ = repos
    issue = IssueEnrollmentToken(
        nodes=nodes,
        tokens=tokens,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        ttl_seconds=_TTL_SECONDS,
    )

    with pytest.raises(NotFoundError):
        await issue.execute(NodeId("node_missing"))


# ---------------------------------------------------------------------------
# EnrollAgent
# ---------------------------------------------------------------------------


async def test_enroll_agent_consumes_token_and_marks_node_online(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    events: EventPublisher,
) -> None:
    nodes, tokens, _ = repos
    node_id = await _register(repos, clock, ids, events)
    issued = await IssueEnrollmentToken(
        nodes=nodes,
        tokens=tokens,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        ttl_seconds=_TTL_SECONDS,
    ).execute(NodeId(node_id))

    clock.advance(60)
    enroll = EnrollAgent(nodes=nodes, tokens=tokens, clock=clock, events=events)
    node = await enroll.execute(
        EnrollAgentCommand(
            plaintext=issued.plaintext,
            agent_version="0.1.0",
            capabilities=NodeCapabilities(ovs_version="3.2.1", interfaces=("eth0",)),
        )
    )

    assert node.status is NodeStatus.ONLINE
    assert node.agent_version == "0.1.0"
    assert node.capabilities is not None
    assert node.capabilities.ovs_version == "3.2.1"

    # Token is now used; second attempt must fail.
    with pytest.raises(ConflictError):
        await enroll.execute(EnrollAgentCommand(plaintext=issued.plaintext))


async def test_enroll_agent_rejects_unknown_token(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
    events: EventPublisher,
) -> None:
    nodes, tokens, _ = repos
    enroll = EnrollAgent(nodes=nodes, tokens=tokens, clock=clock, events=events)

    with pytest.raises(NotFoundError):
        await enroll.execute(EnrollAgentCommand(plaintext="bogus"))


async def test_enroll_agent_rejects_expired_token(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    events: EventPublisher,
) -> None:
    nodes, tokens, _ = repos
    node_id = await _register(repos, clock, ids, events)
    issued = await IssueEnrollmentToken(
        nodes=nodes,
        tokens=tokens,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        ttl_seconds=60,
    ).execute(NodeId(node_id))

    clock.advance(120)  # past TTL
    enroll = EnrollAgent(nodes=nodes, tokens=tokens, clock=clock, events=events)
    with pytest.raises(ConflictError, match="expired"):
        await enroll.execute(EnrollAgentCommand(plaintext=issued.plaintext))


async def test_enroll_agent_validates_non_empty_plaintext(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
    events: EventPublisher,
) -> None:
    nodes, tokens, _ = repos
    enroll = EnrollAgent(nodes=nodes, tokens=tokens, clock=clock, events=events)

    with pytest.raises(ValidationError):
        await enroll.execute(EnrollAgentCommand(plaintext="   "))


# ---------------------------------------------------------------------------
# RecordHeartbeat
# ---------------------------------------------------------------------------


async def test_heartbeat_updates_last_seen_and_capabilities(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    events: EventPublisher,
) -> None:
    nodes, tokens, _ = repos
    node_id = await _register(repos, clock, ids, events)
    issued = await IssueEnrollmentToken(
        nodes=nodes,
        tokens=tokens,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        ttl_seconds=_TTL_SECONDS,
    ).execute(NodeId(node_id))
    await EnrollAgent(nodes=nodes, tokens=tokens, clock=clock, events=events).execute(
        EnrollAgentCommand(plaintext=issued.plaintext)
    )

    clock.advance(30)
    hb = RecordHeartbeat(nodes=nodes, clock=clock)
    node = await hb.execute(
        HeartbeatCommand(
            node_id=NodeId(node_id),
            agent_version="0.2.0",
            capabilities=NodeCapabilities(ovs_version="3.3.0"),
        )
    )

    assert node.agent_version == "0.2.0"
    assert node.capabilities is not None
    assert node.capabilities.ovs_version == "3.3.0"
    assert node.last_seen_at == clock.current


async def test_heartbeat_pending_node_rejected(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
    ids: CountingIdFactory,
    events: EventPublisher,
) -> None:
    nodes, _, _ = repos
    node_id = await _register(repos, clock, ids, events)

    hb = RecordHeartbeat(nodes=nodes, clock=clock)
    with pytest.raises(ValidationError, match="still pending"):
        await hb.execute(HeartbeatCommand(node_id=NodeId(node_id)))


async def test_heartbeat_unknown_node(
    repos: tuple[
        InMemoryNodeRepository,
        InMemoryEnrollmentTokenRepository,
        InMemoryOperationRepository,
    ],
    clock: FrozenClock,
) -> None:
    nodes, _, _ = repos
    hb = RecordHeartbeat(nodes=nodes, clock=clock)

    with pytest.raises(NotFoundError):
        await hb.execute(HeartbeatCommand(node_id=NodeId("node_ghost")))
