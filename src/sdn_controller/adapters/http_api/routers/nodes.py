"""Node endpoints (Milestone 1 read surface).

Enrolment, heartbeat and capability discovery arrive in Milestone 2.
"""

from __future__ import annotations

from fastapi import APIRouter

from sdn_controller.adapters.http_api.dependencies import GetNodeDep, ListNodesDep
from sdn_controller.adapters.http_api.schemas import NodeListResponse, NodeOut
from sdn_controller.core.value_objects.ids import NodeId

router = APIRouter(prefix="/nodes", tags=["nodes"])


@router.get("", response_model=NodeListResponse, summary="List nodes")
async def list_nodes(use_case: ListNodesDep) -> NodeListResponse:
    nodes = await use_case.execute()
    return NodeListResponse(items=[NodeOut.from_domain(n) for n in nodes])


@router.get("/{node_id}", response_model=NodeOut, summary="Get a node")
async def get_node(node_id: str, use_case: GetNodeDep) -> NodeOut:
    node = await use_case.execute(NodeId(node_id))
    return NodeOut.from_domain(node)
