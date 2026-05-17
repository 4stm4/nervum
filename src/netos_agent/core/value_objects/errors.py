"""Domain errors raised by the agent.

Mirror of ``sdn_controller.core.value_objects.errors`` — kept separate so the
agent stays a self-contained package (no implicit cross-import). Adapters
translate to HTTP status / gRPC codes; the core only raises these.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for all agent-level errors."""

    code: str = "agent_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code

    @property
    def message(self) -> str:
        return self.args[0] if self.args else ""


class ValidationError(AgentError):
    """Incoming plan/spec is malformed or violates an invariant."""

    code = "validation_error"


class NotFoundError(AgentError):
    """Requested aggregate (snapshot, bridge, ...) does not exist."""

    code = "not_found"


class OvsdbError(AgentError):
    """Something went wrong while talking to OVSDB.

    Wraps the underlying driver error so adapters can surface a consistent
    code regardless of whether the backend is a subprocess, JSON-RPC, or the
    in-memory fake used in tests.
    """

    code = "ovsdb_error"
