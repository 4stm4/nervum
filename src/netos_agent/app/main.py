"""Process entrypoint for the agent.

* ``app`` — module-level ASGI application for ``uvicorn netos_agent.app.main:app``.
* ``run`` — console script entry, used by ``netos-agent`` (see pyproject).
"""

from __future__ import annotations

import uvicorn

from netos_agent.adapters.http_api import create_app
from netos_agent.app.config import Settings, load_settings
from netos_agent.app.container import build_container
from netos_agent.app.logging import configure_logging


def _bootstrap(settings: Settings | None = None) -> tuple[Settings, object]:
    settings = settings or load_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    container = build_container(settings)
    return settings, create_app(container)


_settings, app = _bootstrap()


def run() -> None:
    uvicorn.run(
        "netos_agent.app.main:app",
        host=_settings.http_host,
        port=_settings.http_port,
        log_config=None,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
