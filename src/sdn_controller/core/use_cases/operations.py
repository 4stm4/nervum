"""Operation read-side use cases.

Write-side transitions live inside ``Operation`` itself (state machine). These
use cases are the public read API used by the HTTP layer to expose operation
progress.
"""

from __future__ import annotations

from sdn_controller.core.entities import Operation
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import OperationId
from sdn_controller.ports.persistence import OperationRepository

_MIN_LIMIT = 1
_MAX_LIMIT = 1000


class GetOperation:
    def __init__(self, *, operations: OperationRepository) -> None:
        self._operations = operations

    async def execute(self, operation_id: OperationId) -> Operation:
        op = await self._operations.get(operation_id)
        if op is None:
            raise NotFoundError(f"operation {operation_id} not found")
        return op


class ListOperations:
    def __init__(self, *, operations: OperationRepository) -> None:
        self._operations = operations

    async def execute(self, *, limit: int = 100) -> list[Operation]:
        clamped = max(_MIN_LIMIT, min(limit, _MAX_LIMIT))
        return await self._operations.list(limit=clamped)
