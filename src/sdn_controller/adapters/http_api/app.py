"""FastAPI application factory.

Why a factory? Tests can build the app with a custom container (e.g. a frozen
clock or deterministic id factory) without touching globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from sdn_controller import __version__
from sdn_controller.adapters.http_api.audit_middleware import AuditMiddleware
from sdn_controller.adapters.http_api.errors import install_exception_handlers
from sdn_controller.adapters.http_api.observability import (
    ObservabilityMiddleware,
    prometheus_metrics,
)
from sdn_controller.adapters.http_api.operation_header import OperationHeaderMiddleware
from sdn_controller.adapters.http_api.rate_limit import (
    RateLimitMiddleware,
    TokenBucketLimiter,
)
from sdn_controller.adapters.http_api.routers import (
    agent as agent_router,
    audit as audit_router,
    backup as backup_router,
    events as events_router,
    health as health_router,
    ipam as ipam_router,
    networks as networks_router,
    nodes as nodes_router,
    operations as operations_router,
    projects as projects_router,
    service_accounts as service_accounts_router,
    snapshots as snapshots_router,
    topology as topology_router,
    webhooks as webhooks_router,
)
from sdn_controller.adapters.http_api.routers.n1 import (
    address_pools_router,
    logical_ports_router,
    qos_policies_router,
    security_groups_router,
    service_objects_router,
)
from sdn_controller.adapters.http_api.routers.n2 import (
    security_policies_router,
    trunk_ports_router,
)
from sdn_controller.adapters.http_api.routers.n3 import (
    bgp_peers_router,
    floating_ips_router,
    routers_router,
)
from sdn_controller.adapters.http_api.routers.n4 import (
    bonds_router,
    listeners_router,
    lbs_router,
    members_router,
    monitors_router,
    pools_router,
    preflight_router,
    quotas_router,
    retention_router,
    snapshots_router as snapshots_n4_router,
)
from sdn_controller.app.container import Container

# N0-05: compat header — tells consumers which envelope schema version they receive.
_SCHEMA_VERSION = "2"


class SchemaVersionMiddleware(BaseHTTPMiddleware):
    """Добавляет ``X-SDN-Schema-Version: 2`` во все ответы API (N0-05).

    Внешние потребители могут гейтиться на эту версию вместо того,
    чтобы разбирать URL-структуру. При будущем bump версии (N1+) они
    получат ``3`` и смогут мигрировать.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:
        response: Response = await call_next(request)  # type: ignore[call-arg]
        response.headers["X-SDN-Schema-Version"] = _SCHEMA_VERSION
        return response

_API_PREFIX = "/api/v1"


def create_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await container.bootstrap()
        if container.settings.background_tasks_enabled:
            container.start_background_tasks()
        try:
            yield
        finally:
            await container.stop_background_tasks()
            await container.shutdown()

    app = FastAPI(
        title="SDN Controller",
        version=__version__,
        description=(
            "Declarative SDN Controller. Northbound REST API for external "
            "management platforms; southbound NetOS Agent API for nodes."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{_API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.container = container

    install_exception_handlers(app)

    # N0-05: compat schema-version header on all responses.
    app.add_middleware(SchemaVersionMiddleware)

    # Middleware регистрируется в стиле «add сначала — выполняется позже»:
    # OperationHeaderMiddleware пишет ответ перед всем (мы ставим
    # ``X-Operation-Id`` непосредственно перед отдачей), AuditMiddleware
    # идёт ПОД observability, чтобы у него уже был request_id в
    # contextvars и principal в request.state. RateLimit регистрируется
    # последним — значит выполняется ПЕРВЫМ, и rejected-запрос даже не
    # доходит до аутентификации и доменной логики.
    app.add_middleware(OperationHeaderMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(ObservabilityMiddleware)
    if container.settings.ratelimit_per_minute > 0:
        capacity = float(container.settings.ratelimit_per_minute)
        limiter = TokenBucketLimiter(
            capacity=capacity,
            refill_rate_per_sec=capacity / 60.0,
            clock=container.clock,
        )
        app.add_middleware(RateLimitMiddleware, limiter=limiter)

    # ``/metrics`` без ``/api/v1`` префикса и без auth — стандартный
    # путь, на который ходит Prometheus-scraper.
    app.add_api_route(
        "/metrics",
        prometheus_metrics,
        methods=["GET"],
        include_in_schema=False,
        summary="Prometheus exposition",
    )

    app.include_router(health_router.router, prefix=_API_PREFIX)
    app.include_router(nodes_router.router, prefix=_API_PREFIX)
    app.include_router(agent_router.router, prefix=_API_PREFIX)
    app.include_router(networks_router.router, prefix=_API_PREFIX)
    app.include_router(operations_router.router, prefix=_API_PREFIX)
    app.include_router(ipam_router.subnets_router, prefix=_API_PREFIX)
    app.include_router(ipam_router.network_subnet_router, prefix=_API_PREFIX)
    app.include_router(ipam_router.allocations_router, prefix=_API_PREFIX)
    app.include_router(topology_router.router, prefix=_API_PREFIX)
    app.include_router(service_accounts_router.accounts_router, prefix=_API_PREFIX)
    app.include_router(service_accounts_router.tokens_router, prefix=_API_PREFIX)
    app.include_router(audit_router.router, prefix=_API_PREFIX)
    app.include_router(backup_router.router, prefix=_API_PREFIX)
    app.include_router(snapshots_router.router, prefix=_API_PREFIX)
    app.include_router(webhooks_router.router, prefix=_API_PREFIX)
    app.include_router(events_router.router, prefix=_API_PREFIX)
    app.include_router(projects_router.router, prefix=_API_PREFIX)
    # N1
    app.include_router(logical_ports_router, prefix=_API_PREFIX)
    app.include_router(security_groups_router, prefix=_API_PREFIX)
    app.include_router(address_pools_router, prefix=_API_PREFIX)
    app.include_router(service_objects_router, prefix=_API_PREFIX)
    app.include_router(qos_policies_router, prefix=_API_PREFIX)
    # N2
    app.include_router(security_policies_router, prefix=_API_PREFIX)
    app.include_router(trunk_ports_router, prefix=_API_PREFIX)
    # N3
    app.include_router(routers_router, prefix=_API_PREFIX)
    app.include_router(floating_ips_router, prefix=_API_PREFIX)
    app.include_router(bgp_peers_router, prefix=_API_PREFIX)
    # N4
    app.include_router(quotas_router, prefix=_API_PREFIX)
    app.include_router(preflight_router, prefix=_API_PREFIX)
    app.include_router(snapshots_n4_router, prefix=_API_PREFIX)
    app.include_router(bonds_router, prefix=_API_PREFIX)
    app.include_router(retention_router, prefix=_API_PREFIX)
    app.include_router(lbs_router, prefix=_API_PREFIX)
    app.include_router(listeners_router, prefix=_API_PREFIX)
    app.include_router(pools_router, prefix=_API_PREFIX)
    app.include_router(members_router, prefix=_API_PREFIX)
    app.include_router(monitors_router, prefix=_API_PREFIX)

    return app
