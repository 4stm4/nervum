"""Observability: request-id, структурные логи, Prometheus-метрики.

Один middleware (``ObservabilityMiddleware``) держит весь поток в куче:

1. Достаёт ``X-Request-Id`` из заголовка (или генерирует фрешный).
2. Биндит ``request_id``, ``method``, ``path`` в ``structlog.contextvars`` —
   с этой точки любой ``structlog.get_logger().info(...)`` ниже по
   стеку получит эти поля автоматически.
3. Замеряет latency, считает HTTP-метрики (rate, latency, status code).
4. Кладёт ``X-Request-Id`` в ответе — чтобы оператор мог сопоставить
   запрос с логами/аудитом.

Метрики — глобальные, экспонируются на ``GET /metrics`` (без auth,
стандартный путь для Prometheus scraper'а).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

import structlog
from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

_REQUEST_ID_HEADER = "x-request-id"

# Имена правил RBAC возвращают эти коды; вынесено отдельными константами,
# чтобы ruff не жаловался на «магические числа».
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403

# Все метрики живут на отдельном реестре, чтобы в тестах их можно было
# изолированно собирать (например, тестируя только нашу регистрацию).
# Но для прод-сборки мы используем дефолтный ``REGISTRY``: scraper
# забирает его одним запросом.
_DEFAULT_REGISTRY: CollectorRegistry = REGISTRY


# Buckets подобраны под характерные времена SDN-операций (от десятков
# миллисекунд до нескольких секунд apply'а на десятки узлов).
_LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


@dataclass(slots=True)
class _Metrics:
    requests_total: Counter
    request_duration_seconds: Histogram
    auth_failures_total: Counter


def _build_metrics(registry: CollectorRegistry) -> _Metrics:
    """Создаём все метрики. Вынесено в helper, чтобы тесты могли
    создавать свой ``CollectorRegistry`` и не зависели от глобального."""
    return _Metrics(
        requests_total=Counter(
            "sdn_http_requests_total",
            "HTTP-запросов к northbound API, по методу/пути/статусу.",
            labelnames=("method", "path", "status"),
            registry=registry,
        ),
        request_duration_seconds=Histogram(
            "sdn_http_request_duration_seconds",
            "Длительность обработки HTTP-запроса.",
            labelnames=("method", "path"),
            buckets=_LATENCY_BUCKETS,
            registry=registry,
        ),
        auth_failures_total=Counter(
            "sdn_auth_failures_total",
            "Запросы, отбитые middleware'ом аутентификации.",
            labelnames=("reason",),  # "unauthorized" / "forbidden"
            registry=registry,
        ),
    )


# Лениво-инициализированный набор метрик. ``REGISTRY`` единый на процесс,
# повторная регистрация той же метрики падает — обернуто в try/except.
_METRICS: _Metrics | None = None


def get_metrics() -> _Metrics:
    global _METRICS  # noqa: PLW0603 — кеш для процесса
    if _METRICS is None:
        try:
            _METRICS = _build_metrics(_DEFAULT_REGISTRY)
        except ValueError:
            # Тесты могут импортировать модуль повторно — забираем уже
            # зарегистрированные метрики обратно из реестра.
            collectors = _DEFAULT_REGISTRY._names_to_collectors
            _METRICS = _Metrics(
                requests_total=cast(Counter, collectors["sdn_http_requests_total"]),
                request_duration_seconds=cast(
                    Histogram, collectors["sdn_http_request_duration_seconds"]
                ),
                auth_failures_total=cast(Counter, collectors["sdn_auth_failures_total"]),
            )
    return _METRICS


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Корреляция + структурные логи + Prometheus-метрики на каждый запрос.

    Заголовок ``X-Request-Id`` либо берётся из запроса, либо генерится
    новым. Возвращается в ответе тем же именем, чтобы клиент мог
    сопоставить запрос с серверными логами/аудитом.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or uuid.uuid4().hex
        method = request.method

        # Чистим контекст между запросами и связываем стандартные поля.
        # Все логи внутри хендлера автоматически получат эти поля через
        # ``structlog.contextvars.merge_contextvars``.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            http_method=method,
            http_path=request.url.path,
        )

        metrics = get_metrics()
        started = time.perf_counter()
        status_code = 500  # по умолчанию — на случай неотловленного исключения
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[_REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration = time.perf_counter() - started
            path = _route_template(request)
            metrics.request_duration_seconds.labels(method=method, path=path).observe(duration)
            metrics.requests_total.labels(method=method, path=path, status=str(status_code)).inc()
            if status_code == _HTTP_UNAUTHORIZED:
                metrics.auth_failures_total.labels(reason="unauthorized").inc()
            elif status_code == _HTTP_FORBIDDEN:
                metrics.auth_failures_total.labels(reason="forbidden").inc()


def _route_template(request: Request) -> str:
    """Шаблон маршрута (``/networks/{network_id}``) вместо конкретного
    URL'а — нужно, чтобы кардинальность метрик не взрывалась."""
    route = request.scope.get("route")
    if route is not None and hasattr(route, "path"):
        return str(route.path)
    return request.url.path


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------


async def prometheus_metrics() -> PlainTextResponse:
    """``GET /metrics`` — стандартный Prometheus exposition format."""
    payload = generate_latest(_DEFAULT_REGISTRY)
    return PlainTextResponse(content=payload, media_type=CONTENT_TYPE_LATEST)


__all__ = ["ObservabilityMiddleware", "get_metrics", "prometheus_metrics"]
