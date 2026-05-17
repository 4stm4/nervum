"""Agent-facing endpoints — enrolment and heartbeat.

Today the proof of agent identity is:

* on ``/enroll`` — an unspent enrolment token (issued by an operator);
* on ``/heartbeat`` — the ``node_id`` previously bound to that token.

That's deliberately weak: M9 wraps both endpoints with mTLS so the agent's
identity is the certificate it presents. We keep the agent surface in its
own router so that auth boundary lives in one place.
"""

from __future__ import annotations

from fastapi import APIRouter

from sdn_controller.adapters.http_api.dependencies import (
    EnrollAgentDep,
    RecordHeartbeatDep,
)
from sdn_controller.adapters.http_api.schemas import (
    AgentEnrollRequest,
    AgentEnrollResponse,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    NodeOut,
)
from sdn_controller.core.use_cases.enrollment import (
    EnrollAgentCommand,
    HeartbeatCommand,
)
from sdn_controller.core.value_objects.ids import NodeId

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post(
    "/enroll",
    response_model=AgentEnrollResponse,
    summary="Agent presents an enrolment token; controller binds it to a node",
)
async def enroll(
    payload: AgentEnrollRequest,
    use_case: EnrollAgentDep,
) -> AgentEnrollResponse:
    node = await use_case.execute(
        EnrollAgentCommand(
            plaintext=payload.token,
            agent_version=payload.agent_version,
            capabilities=(
                payload.capabilities.to_domain() if payload.capabilities is not None else None
            ),
        )
    )
    return AgentEnrollResponse(node=NodeOut.from_domain(node))


@router.post(
    "/heartbeat",
    response_model=AgentHeartbeatResponse,
    summary="Agent reports liveness; refreshes ``last_seen_at``/capabilities",
)
async def heartbeat(
    payload: AgentHeartbeatRequest,
    use_case: RecordHeartbeatDep,
) -> AgentHeartbeatResponse:
    node = await use_case.execute(
        HeartbeatCommand(
            node_id=NodeId(payload.node_id),
            agent_version=payload.agent_version,
            capabilities=(
                payload.capabilities.to_domain() if payload.capabilities is not None else None
            ),
        )
    )
    return AgentHeartbeatResponse(node=NodeOut.from_domain(node))
