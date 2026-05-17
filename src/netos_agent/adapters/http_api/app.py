"""FastAPI application factory for the agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from netos_agent import __version__
from netos_agent.adapters.http_api.errors import install_exception_handlers
from netos_agent.adapters.http_api.routers import (
    health as health_router,
    network as network_router,
    node as node_router,
    ovs as ovs_router,
    system as system_router,
)
from netos_agent.app.container import Container


def create_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await container.shutdown()

    app = FastAPI(
        title="NetOS Agent",
        version=__version__,
        description=(
            "Local OVS executor for the SDN Controller. "
            "Accepts structured plans, returns per-step idempotent results, "
            "and persists snapshots for rollback."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.container = container

    install_exception_handlers(app)

    # Health probes live at the root (no /v1 prefix) so probes don't break
    # when we rev the API version.
    app.include_router(health_router.router)
    app.include_router(node_router.router)
    app.include_router(ovs_router.router)
    app.include_router(network_router.router)
    app.include_router(system_router.router)

    return app
