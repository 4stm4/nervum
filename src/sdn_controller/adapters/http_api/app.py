"""FastAPI application factory.

Why a factory? Tests can build the app with a custom container (e.g. a frozen
clock or deterministic id factory) without touching globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
    health as health_router,
    ipam as ipam_router,
    networks as networks_router,
    nodes as nodes_router,
    operations as operations_router,
    service_accounts as service_accounts_router,
    snapshots as snapshots_router,
    topology as topology_router,
)
from sdn_controller.app.container import Container

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

    return app
