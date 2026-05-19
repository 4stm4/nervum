"""Domain → HTTP error mapping.

Every domain exception is translated to a stable error envelope::

    {"error": {"code": "...", "message": "...", "details": {}}}

We never expose stack traces or internal repr; that's a security boundary as
much as an ergonomics one.
"""

from __future__ import annotations

from typing import Final

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sdn_controller.adapters.http_api.schemas import ErrorBody, ErrorResponse
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    InvalidStateTransition,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)

_log = structlog.get_logger(__name__)

# 422 was renamed in modern Starlette (UNPROCESSABLE_ENTITY → UNPROCESSABLE_CONTENT).
# Use a numeric literal so we work on both spellings without deprecation warnings.
_HTTP_422 = 422

_STATUS_BY_TYPE: Final[dict[type[DomainError], int]] = {
    ValidationError: status.HTTP_400_BAD_REQUEST,
    UnauthorizedError: status.HTTP_401_UNAUTHORIZED,
    ForbiddenError: status.HTTP_403_FORBIDDEN,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidStateTransition: status.HTTP_409_CONFLICT,
    RateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
}


def _status_for(exc: DomainError) -> int:
    return _STATUS_BY_TYPE.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)


def _details_from(exc: DomainError) -> dict[str, object]:
    """Use cases stash structured detail as ``exc.args[1]`` — surface it if present."""
    if len(exc.args) > 1 and isinstance(exc.args[1], dict):
        return dict(exc.args[1])
    return {}


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        http_status = _status_for(exc)
        details = _details_from(exc)
        _log.info(
            "domain_error",
            code=exc.code,
            message=exc.message,
            http_status=http_status,
        )
        body = ErrorResponse(error=ErrorBody(code=exc.code, message=exc.message, details=details))
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
