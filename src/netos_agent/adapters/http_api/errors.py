"""Domain → HTTP error mapping for the agent."""

from __future__ import annotations

from typing import Final

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from netos_agent.adapters.http_api.schemas import ErrorBody, ErrorResponse
from netos_agent.core.value_objects.errors import (
    AgentError,
    NotFoundError,
    OvsdbError,
    ValidationError,
)

_log = structlog.get_logger(__name__)

# Starlette renamed 422 — use the numeric literal to dodge the deprecation
# warning across versions.
_HTTP_422 = 422

_STATUS_BY_TYPE: Final[dict[type[AgentError], int]] = {
    ValidationError: status.HTTP_400_BAD_REQUEST,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    OvsdbError: status.HTTP_502_BAD_GATEWAY,  # backend is unreachable / refused
}


def _status_for(exc: AgentError) -> int:
    return _STATUS_BY_TYPE.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AgentError)
    async def _agent_error(_: Request, exc: AgentError) -> JSONResponse:
        http_status = _status_for(exc)
        _log.info(
            "agent_error",
            code=exc.code,
            message=exc.message,
            http_status=http_status,
        )
        body = ErrorResponse(error=ErrorBody(code=exc.code, message=exc.message))
        return JSONResponse(status_code=http_status, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        body = ErrorResponse(
            error=ErrorBody(
                code="request_validation_error",
                message="request body or parameters failed validation",
                details={"errors": exc.errors()},
            )
        )
        return JSONResponse(status_code=_HTTP_422, content=body.model_dump())
