"""Admin endpoints for the node lifecycle.

Reads (``GET``) plus operator-driven mutations (register, remove, issue
enrolment token). Agent-side flows live in ``routers.agent`` so that the auth
boundary can diverge later — admin endpoints will be RBAC-gated; agent
endpoints will move to mTLS.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from sdn_controller.adapters.http_api.dependencies import (
    GetNodeDep,
    IssueEnrollmentTokenDep,
    ListNodesDep,
    RegisterNodeDep,
    RemoveNodeDep,
)
from sdn_controller.adapters.http_api.schemas import (
    EnrollmentTokenIssueResponse,
    NodeListResponse,
    NodeOut,
    NodeRegisterRequest,
    NodeRegisterResponse,
    OperationEnvelope,
    operation_envelope,
)
from sdn_controller.core.use_cases.nodes import RegisterNodeCommand
from sdn_controller.core.value_objects.ids import NodeId

router = APIRouter(prefix="/nodes", tags=["nodes"])


@router.get("", response_model=NodeListResponse, summary="List nodes")
async def list_nodes(use_case: ListNodesDep) -> NodeListResponse:
    nodes = await use_case.execute()
    return NodeListResponse(items=[NodeOut.from_domain(n) for n in nodes])


@router.post(
    "",
    response_model=NodeRegisterResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Register a pending node (operator step before agent enrolment)",
)
async def register_node(
    payload: NodeRegisterRequest,
    use_case: RegisterNodeDep,
) -> NodeRegisterResponse:
    result = await use_case.execute(
        RegisterNodeCommand(
            name=payload.name,
            mgmt_ip=payload.mgmt_ip,
            roles=list(payload.roles),
            labels=dict(payload.labels),
        )
    )
    return NodeRegisterResponse(
        node=NodeOut.from_domain(result.node),
        operation=operation_envelope(result.operation),
    )


@router.get("/{node_id}", response_model=NodeOut, summary="Get a node")
async def get_node(node_id: str, use_case: GetNodeDep) -> NodeOut:
    node = await use_case.execute(NodeId(node_id))
    return NodeOut.from_domain(node)


@router.delete(
    "/{node_id}",
    response_model=OperationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Remove a node and its enrolment tokens",
)
async def delete_node(node_id: str, use_case: RemoveNodeDep) -> OperationEnvelope:
    op = await use_case.execute(NodeId(node_id))
    return operation_envelope(op)


@router.post(
    "/{node_id}/enroll-token",
    response_model=EnrollmentTokenIssueResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a one-shot enrolment token for a pending node",
)
async def issue_enrollment_token(
    node_id: str,
    use_case: IssueEnrollmentTokenDep,
) -> EnrollmentTokenIssueResponse:
    result = await use_case.execute(NodeId(node_id))
    return EnrollmentTokenIssueResponse(
        token=result.plaintext,
        token_id=result.token.id,
        node_id=result.token.node_id,
        expires_at=result.token.expires_at,
        issued_at=result.token.issued_at,
    )
