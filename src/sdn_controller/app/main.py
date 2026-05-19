"""Process entrypoint.

* ``app`` — module-level ASGI application for ``uvicorn sdn_controller.app.main:app``.
* ``run`` — console script entry, used by ``sdn-controller`` (see pyproject).
"""

from __future__ import annotations

import ssl
from typing import Any

import uvicorn

from sdn_controller.adapters.http_api import create_app
from sdn_controller.app.config import Settings, load_settings
from sdn_controller.app.container import build_container
from sdn_controller.app.logging import configure_logging
from sdn_controller.app.tracing import configure_tracing, instrument_fastapi


def _bootstrap(settings: Settings | None = None) -> tuple[Settings, object]:
    settings = settings or load_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    # Tracing нужно поднять ДО create_app: httpx-инструментатор
    # цепляется к глобальному ``httpx.AsyncClient``, а FastAPI-
    # инструментатор будет применён уже к готовому app'у.
    configure_tracing(settings)
    container = build_container(settings)
    app = create_app(container)
    instrument_fastapi(app)
    return settings, app


# ASGI app for ``uvicorn``.
_settings, app = _bootstrap()


def _tls_kwargs(settings: Settings) -> dict[str, Any]:
    """Параметры TLS для ``uvicorn.run``. Пусто, если TLS выключен.

    Без CA-файла mTLS не включается даже при ``tls_require_client_cert``
    — проверка ничего бы не смогла валидировать. Лучше упасть рано.
    """
    if not settings.tls_enabled:
        return {}
    if not settings.tls_cert_file or not settings.tls_key_file:
        raise RuntimeError(
            "SDN_TLS_ENABLED=true requires SDN_TLS_CERT_FILE and SDN_TLS_KEY_FILE",
        )
    kwargs: dict[str, Any] = {
        "ssl_certfile": settings.tls_cert_file,
        "ssl_keyfile": settings.tls_key_file,
    }
    if settings.tls_ca_file:
        kwargs["ssl_ca_certs"] = settings.tls_ca_file
    if settings.tls_require_client_cert:
        if not settings.tls_ca_file:
            raise RuntimeError(
                "SDN_TLS_REQUIRE_CLIENT_CERT=true requires SDN_TLS_CA_FILE",
            )
        kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
    return kwargs


def run() -> None:
    """Console-script entry point (``sdn-controller``)."""
    uvicorn.run(
        "sdn_controller.app.main:app",
        host=_settings.http_host,
        port=_settings.http_port,
        log_config=None,  # we install our own logging.
        **_tls_kwargs(_settings),
    )


if __name__ == "__main__":  # pragma: no cover
    run()
