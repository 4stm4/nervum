"""Domain errors.

These are **domain** exceptions — they have no HTTP semantics. Adapters (e.g.
the FastAPI layer) translate them into protocol-specific responses. Keeping
HTTP status codes out of the core lets us re-use the same use cases from gRPC,
CLI, or background jobs.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base for all domain-level errors."""

    code: str = "domain_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code

    @property
    def message(self) -> str:
        return self.args[0] if self.args else ""


class ValidationError(DomainError):
    """The incoming intent is malformed or violates an invariant."""

    code = "validation_error"


class NotFoundError(DomainError):
    """The requested aggregate does not exist."""

    code = "not_found"


class ConflictError(DomainError):
    """The intent conflicts with an existing aggregate (unique name, vni, ...)."""

    code = "conflict"


class InvalidStateTransition(DomainError):
    """An aggregate cannot move into the requested state from its current one."""

    code = "invalid_state_transition"


class UnauthorizedError(DomainError):
    """Запрос пришёл без валидной аутентификации (Bearer / mTLS / ...)."""

    code = "unauthorized"


class ForbiddenError(DomainError):
    """Аутентификация прошла, но у принципала нет нужного права."""

    code = "forbidden"


class RateLimitedError(DomainError):
    """Принципал превысил квоту запросов в минуту (SDN-042)."""

    code = "rate_limited"
