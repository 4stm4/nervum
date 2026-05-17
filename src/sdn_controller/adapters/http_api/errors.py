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
    InvalidStateTransition,
    NotFoundError,
    ValidationError,
)

_log = structlog.get_logger(__name__)

# 422 was renamed in modern Starlette (UNPROCESSABLE_ENTITY → UNPROCESSABLE_CONTENT).
# Use a numeric literal so we work on both spellings without deprecation warnings.
_HTTP_422 = 422

_STATUS_BY_TYPE: Final[dict[type[DomainError], int]] = {
    ValidationError: status.HTTP_400_BAD_REQUEST,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidStateTransition: status.HTTP_409_CONFLICT,
}


def _status_for(exc: DomainError) -> int:
    return _STATUS_BY_TYPE.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        http_status = _status_for(exc)
        _log.info(
            "domain_error",
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
