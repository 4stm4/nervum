"""Middleware: подставить ``X-Operation-Id`` в ответ.

Для внешнего orchestrator'а (testum) важно сразу после ответа знать
``operation_id`` без парсинга тела. Эндпоинты, которые вызывают
operation-ориентированные use case'ы, кладут ``operation_id`` в
``request.state.operation_id`` — middleware превращает его в
response-заголовок.

Альтернатива была бы парсить JSON-тело middleware'ом — это дорого
(буферизация всего payload'а) и хрупко (тело меняет shape по
endpoint'у). Лучше явно проставлять id из хендлеров.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

_OPERATION_HEADER = "x-operation-id"


class OperationHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        operation_id = getattr(request.state, "operation_id", None)
        if operation_id:
            response.headers[_OPERATION_HEADER] = str(operation_id)
        return response


__all__ = ["OperationHeaderMiddleware"]
