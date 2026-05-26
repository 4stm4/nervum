"""OpenTelemetry tracing setup (SDN-041).

* ``configure_tracing(settings)`` — единый entry point, дёргается в
  ``main._bootstrap`` после ``configure_logging``. Если
  ``otel_enabled=False`` (default), функция — no-op, и ``tracer()``
  отдаёт API-стаб (он же в otel-api как `NoOpTracer`).
* Auto-instrumentation для FastAPI / httpx / SQLAlchemy — best-effort:
  если соответствующий пакет не установлен (нет ``[otel]`` extra),
  инструментатор не подключается, остальное продолжает работать.
* Custom span'ы для долгих доменных операций (``ApplyNetwork``,
  webhook delivery) — см. ``tracer().start_as_current_span(...)`` в
  use case'ах. Auto-instrumentation покрывает «протокольные» границы
  (request, DB-запрос); custom span'ы — «бизнес-границы».

Mypy: opentelemetry-* пакеты не все строго типизированы — точки
интеграции аккуратно завёрнуты, чтобы строгий mypy не разрывал
сборку при отсутствующих stub'ах.
"""

from __future__ import annotations

import contextlib
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.trace import Tracer

from sdn_controller import __version__
from sdn_controller.app.config import Settings

_log = structlog.get_logger(__name__)
_TRACER_NAME = "sdn-controller"
_configured: bool = False


def tracer() -> Tracer:
    """Глобальный tracer. Безопасен, даже если ``configure_tracing``
    не дёргался: вернёт NoOp-trace из ``opentelemetry.trace``.
    """
    return trace.get_tracer(_TRACER_NAME, __version__)


def configure_tracing(settings: Settings) -> None:
    """Идемпотентная настройка. Повторный вызов — no-op."""
    global _configured  # noqa: PLW0603 — singleton per-process
    if _configured:
        return
    if not settings.otel_enabled:
        _configured = True  # запоминаем, чтобы повторный вызов не делал ничего
        return

    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
    from opentelemetry.sdk.trace.sampling import (  # noqa: PLC0415
        ParentBased,
        TraceIdRatioBased,
    )

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": __version__,
            "deployment.environment": settings.env,
        }
    )
    sampler = ParentBased(root=TraceIdRatioBased(settings.otel_sample_rate))
    provider = TracerProvider(resource=resource, sampler=sampler)

    # OTLP HTTP exporter — best-effort: если пакет не установлен,
    # tracing остаётся включённым (NoOp processor), но события никуда
    # не уйдут. Это лучше fatal-крэша при missing extra.
    exporter = _build_otlp_exporter(settings)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    _apply_auto_instrumentation(settings)
    _configured = True
    _log.info(
        "tracing_configured",
        otel_enabled=True,
        endpoint=settings.otel_exporter_otlp_endpoint,
        sample_rate=settings.otel_sample_rate,
    )


def _build_otlp_exporter(settings: Settings) -> Any | None:
    if not settings.otel_exporter_otlp_endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
    except ImportError:
        _log.warning(
            "otel_exporter_missing",
            hint="install with the [otel] extra to enable OTLP export",
        )
        return None
    return OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)


def _apply_auto_instrumentation(settings: Settings) -> None:
    """Подключаем инструментаторы httpx и SQLAlchemy.

    FastAPI инструментируется отдельной функцией ``instrument_fastapi``
    (см. ниже): её надо вызвать **после** создания ``app``, потому что
    FastAPI-инструментатор работает по экземпляру.
    """
    with contextlib.suppress(ImportError):
        from opentelemetry.instrumentation.httpx import (  # noqa: PLC0415
            HTTPXClientInstrumentor,
        )

        HTTPXClientInstrumentor().instrument()
    if settings.persistence in {"sqlite", "postgres"}:
        with contextlib.suppress(ImportError):
            from opentelemetry.instrumentation.sqlalchemy import (  # noqa: PLC0415
                SQLAlchemyInstrumentor,
            )

            SQLAlchemyInstrumentor().instrument()


def instrument_fastapi(app: Any) -> None:
    """Auto-instrument FastAPI-приложение. Безопасно при выключенном
    tracing (NoOp tracer just generates throw-away spans)."""
    with contextlib.suppress(ImportError):
        from opentelemetry.instrumentation.fastapi import (  # noqa: PLC0415
            FastAPIInstrumentor,
        )

        FastAPIInstrumentor.instrument_app(app)


def reset_for_tests() -> None:
    """Сбросить флаг configured + tracer provider. Только для тестов."""
    global _configured  # noqa: PLW0603 — test helper
    _configured = False
    trace.set_tracer_provider(trace.NoOpTracerProvider())


__all__ = [
    "configure_tracing",
    "instrument_fastapi",
    "reset_for_tests",
    "tracer",
]
