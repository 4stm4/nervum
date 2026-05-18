"""Аудит-middleware: пишет ``AuditEvent`` после каждого mutating-запроса.

Подключается **под** ``ObservabilityMiddleware`` (то есть выполняется
позже), чтобы:
* ``request_id`` уже лежал в ``structlog.contextvars``;
* `principal` уже был положен ``current_principal`` в ``request.state``.

Action выводится из (method, шаблон-маршрута) по простому правилу:
``<resource>.<verb>``. Это не «универсальная» схема — она специально
заточена под текущую northbound-поверхность, и обновляется руками
при добавлении новых ручек.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from sdn_controller.app.container import Container
from sdn_controller.core.entities import Principal
from sdn_controller.core.use_cases.audit import RecordAuditCommand

_log = structlog.get_logger(__name__)


# Шаблон → (action, resource_type, resource_id_path_param | None).
# Если путь в шаблоне имеет ``{X}`` — будем читать значение из
# ``request.path_params[X]`` для ``resource_id``.
_RULES: tuple[tuple[str, str, str, str, str | None], ...] = (
    # method, path template, action, resource_type, resource_id_param
    ("POST", "/api/v1/networks", "network.create", "network", None),
    ("PATCH", "/api/v1/networks/{network_id}", "network.update", "network", "network_id"),
    (
        "POST",
        "/api/v1/networks/{network_id}/nodes",
        "network.assign_nodes",
        "network",
        "network_id",
    ),
    (
        "POST",
        "/api/v1/networks/{network_id}/apply",
        "network.apply",
        "network",
        "network_id",
    ),
    (
        "POST",
        "/api/v1/networks/{network_id}/subnet",
        "subnet.upsert",
        "network",
        "network_id",
    ),
    ("POST", "/api/v1/nodes", "node.register", "node", None),
    ("DELETE", "/api/v1/nodes/{node_id}", "node.remove", "node", "node_id"),
    (
        "POST",
        "/api/v1/nodes/{node_id}/enroll-token",
        "enrollment_token.issue",
        "node",
        "node_id",
    ),
    ("POST", "/api/v1/agent/enroll", "agent.enroll", "node", None),
    ("POST", "/api/v1/agent/heartbeat", "agent.heartbeat", "node", None),
    ("POST", "/api/v1/service-accounts", "service_account.create", "service_account", None),
    (
        "POST",
        "/api/v1/service-accounts/{account_id}/disable",
        "service_account.disable",
        "service_account",
        "account_id",
    ),
    (
        "POST",
        "/api/v1/service-accounts/{account_id}/tokens",
        "service_token.issue",
        "service_account",
        "account_id",
    ),
    (
        "POST",
        "/api/v1/service-tokens/{token_id}/revoke",
        "service_token.revoke",
        "service_token",
        "token_id",
    ),
    (
        "POST",
        "/api/v1/subnets/{subnet_id}/allocations",
        "ip_allocation.create",
        "subnet",
        "subnet_id",
    ),
    (
        "DELETE",
        "/api/v1/allocations/{allocation_id}",
        "ip_allocation.release",
        "ip_allocation",
        "allocation_id",
    ),
)


def _match_rule(method: str, route_path: str) -> tuple[str, str, str | None] | None:
    """Найти правило: action, resource_type, имя path-параметра для id."""
    for m, tmpl, action, res, id_param in _RULES:
        if m == method and tmpl == route_path:
            return action, res, id_param
    return None


_REQUEST_ID_KEY = "request_id"


class AuditMiddleware(BaseHTTPMiddleware):
    """Пишет один ``AuditEvent`` на каждый mutating-запрос."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)

        # Записываем аудит только для известных нам ручек. Для всего
        # остального (например, GET /metrics) шум не интересен.
        route = request.scope.get("route")
        route_path = getattr(route, "path", "") if route is not None else ""
        matched = _match_rule(request.method, route_path)
        if matched is None:
            return response

        action, resource_type, id_param = matched
        resource_id: str | None = None
        if id_param is not None:
            raw = request.path_params.get(id_param)
            resource_id = str(raw) if raw is not None else None

        principal: Principal | None = getattr(request.state, "principal", None)
        actor = principal.name if principal is not None else None

        container: Container = request.app.state.container
        # structlog уже знает request_id из ObservabilityMiddleware'а.
        ctx = structlog.contextvars.get_contextvars()
        request_id = ctx.get(_REQUEST_ID_KEY)

        try:
            await container.record_audit.execute(
                RecordAuditCommand(
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    actor=actor,
                    http_status=response.status_code,
                    request_id=request_id,
                )
            )
        except Exception as exc:
            _log.warning(
                "audit_write_failed",
                action=action,
                resource_type=resource_type,
                error=str(exc),
            )
        return response


__all__ = ["AuditMiddleware"]
