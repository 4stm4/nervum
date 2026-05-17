"""Node read-side use cases.

Enrolment, heartbeats, and lifecycle transitions land in Milestone 2. For now
we only expose listing/lookup so the HTTP API has a complete read surface.
"""

from __future__ import annotations

from sdn_controller.core.entities import Node
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import NodeId
from sdn_controller.ports.persistence import NodeRepository


class ListNodes:
    def __init__(self, *, nodes: NodeRepository) -> None:
        self._nodes = nodes

    async def execute(self) -> list[Node]:
        return await self._nodes.list()


class GetNode:
    def __init__(self, *, nodes: NodeRepository) -> None:
        self._nodes = nodes

    async def execute(self, node_id: NodeId) -> Node:
        node = await self._nodes.get(node_id)
        if node is None:
            raise NotFoundError(f"node {node_id} not found")
        return node
