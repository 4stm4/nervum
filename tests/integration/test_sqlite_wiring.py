"""End-to-end smoke test through ``build_container`` with SQLite.

The other suites either go through in-memory adapters (HTTP API tests) or hit
the SQL repositories directly. This test stitches the two together: it builds
the production container with ``persistence="sqlite"``, drives a request
through the FastAPI app, and confirms the response was actually persisted to
the database file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.sql import build_engine
from sdn_controller.adapters.sql.models import Base
from sdn_controller.app.config import Settings
from sdn_controller.app.container import Container, build_container


@pytest.fixture
async def sqlite_container(tmp_path: Path) -> AsyncIterator[Container]:
    db = tmp_path / "wiring.db"
    url = f"sqlite+aiosqlite:///{db}"

    # Bootstrap schema with a throwaway engine, then let the container open
    # its own engine against the same file. The Alembic migration is the
    # production path; using ``metadata.create_all`` keeps the smoke fast.
    bootstrap_engine = build_engine(url)
    async with bootstrap_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_engine.dispose()

    settings = Settings(
        persistence="sqlite",
        database_url=url,
        log_level="WARNING",
        log_format="console",
    )
    container = build_container(settings)
    try:
        yield container
    finally:
        await container.shutdown()


def test_create_network_persists_through_sqlite(sqlite_container: Container) -> None:
    with TestClient(create_app(sqlite_container)) as client:
        r = client.post(
            "/api/v1/networks",
            json={"name": "tenant-sqlite", "type": "vxlan", "vni": 42},
        )
        assert r.status_code == 202, r.text
        network_id = r.json()["network"]["id"]

        # Read-back path uses a fresh session, so a successful GET proves the
        # write was committed (not just held in the session cache).
        got = client.get(f"/api/v1/networks/{network_id}")
        assert got.status_code == 200
        assert got.json()["name"] == "tenant-sqlite"
        assert got.json()["vni"] == 42
