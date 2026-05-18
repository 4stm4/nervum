"""FastAPI application factory.

Why a factory? Tests can build the app with a custom container (e.g. a frozen
clock or deterministic id factory) without touching globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sdn_controller import __version__
from sdn_controller.adapters.http_api.errors import install_exception_handlers
from sdn_controller.adapters.http_api.routers import (
    agent as agent_router,
    health as health_router,
    ipam as ipam_router,
    networks as networks_router,
    nodes as nodes_router,
    operations as operations_router,
)
from sdn_controller.app.container import Container

_API_PREFIX = "/api/v1"


def create_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup hooks (engine connection check, migrations bootstrap, ...)
        # belong here. Today the engine is created eagerly in the container,
        # so we only need a clean shutdown path.
        try:
            yield
        finally:
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

    app.include_router(health_router.router, prefix=_API_PREFIX)
    app.include_router(nodes_router.router, prefix=_API_PREFIX)
    app.include_router(agent_router.router, prefix=_API_PREFIX)
    app.include_router(networks_router.router, prefix=_API_PREFIX)
    app.include_router(operations_router.router, prefix=_API_PREFIX)
    app.include_router(ipam_router.subnets_router, prefix=_API_PREFIX)
    app.include_router(ipam_router.network_subnet_router, prefix=_API_PREFIX)
    app.include_router(ipam_router.allocations_router, prefix=_API_PREFIX)

    return app
