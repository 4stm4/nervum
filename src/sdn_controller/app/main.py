"""Process entrypoint.

* ``app`` — module-level ASGI application for ``uvicorn sdn_controller.app.main:app``.
* ``run`` — console script entry, used by ``sdn-controller`` (see pyproject).
"""

from __future__ import annotations

import uvicorn

from sdn_controller.adapters.http_api import create_app
from sdn_controller.app.config import Settings, load_settings
from sdn_controller.app.container import build_container
from sdn_controller.app.logging import configure_logging


def _bootstrap(settings: Settings | None = None) -> tuple[Settings, object]:
    settings = settings or load_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    container = build_container(settings)
    return settings, create_app(container)


# ASGI app for ``uvicorn``.
_settings, app = _bootstrap()


def run() -> None:
    """Console-script entry point (``sdn-controller``)."""
    uvicorn.run(
        "sdn_controller.app.main:app",
        host=_settings.http_host,
        port=_settings.http_port,
        log_config=None,  # we install our own logging.
    )


if __name__ == "__main__":  # pragma: no cover
    run()
