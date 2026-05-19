"""Token-bucket rate limit per principal (SDN-042).

Простой token bucket в памяти:

* ``capacity`` = ``ratelimit_per_minute``;
* ``refill_rate`` = ``capacity / 60.0`` токенов в секунду;
* ключ — sha256-префикс ``Authorization``-токена, либо client IP, либо
  ``"anon"`` (когда нет ни того, ни другого).

Этого хватает для single-replica deployment. Multi-replica + distributed
quota — отдельный backend (Redis/Postgres), к которому подключим тот же
порт позже (не в M13).

429-ответ ходит мимо обычного DomainError-обработчика — это middleware,
нам важно не потерять ``X-Request-Id`` и метрики, поэтому возвращаем
``JSONResponse`` сами.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anyio
import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import REGISTRY, Counter
from starlette.middleware.base import BaseHTTPMiddleware

from sdn_controller.adapters.http_api.schemas import ErrorBody, ErrorResponse
from sdn_controller.core.services.clock import Clock

_log = structlog.get_logger(__name__)
_HTTP_TOO_MANY_REQUESTS = 429


def _build_rejections_counter() -> Counter:
    try:
        return Counter(
            "sdn_rate_limit_rejections_total",
            "Запросы, отбитые rate-limit middleware'ом (per-principal).",
            labelnames=("principal_kind",),
            registry=REGISTRY,
        )
    except ValueError:
        collectors = REGISTRY._names_to_collectors
        return collectors["sdn_rate_limit_rejections_total"]  # type: ignore[return-value]


_REJECTIONS = _build_rejections_counter()


@dataclass(slots=True)
class _Bucket:
    tokens: float
    last_refill: float  # секунды с момента clock.now() при первом обращении


class TokenBucketLimiter:
    """In-memory лимитер. Один экземпляр живёт всё время процесса."""

    def __init__(self, *, capacity: float, refill_rate_per_sec: float, clock: Clock) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate_per_sec
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._mutex = anyio.Lock()

    async def acquire(self, key: str) -> bool:
        now = self._clock.now().timestamp()
        async with self._mutex:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self._capacity, last_refill=now)
                self._buckets[key] = bucket
            # Refill пропорционально прошедшему времени, но не выше capacity.
            elapsed = max(0.0, now - bucket.last_refill)
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_rate)
            bucket.last_refill = now
            if bucket.tokens < 1.0:
                return False
            bucket.tokens -= 1.0
            return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-principal token bucket; 429 + structured body на превышение."""

    def __init__(self, app: object, *, limiter: TokenBucketLimiter) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._limiter = limiter

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        key, kind = _principal_key(request)
        if await self._limiter.acquire(key):
            return await call_next(request)

        _log.info("rate_limit_rejected", principal_kind=kind)
        _REJECTIONS.labels(principal_kind=kind).inc()
        body = ErrorResponse(
            error=ErrorBody(
                code="rate_limited",
                message="too many requests, slow down",
                details={"retry_after_seconds": 60},
            )
        )
        response = JSONResponse(
            status_code=_HTTP_TOO_MANY_REQUESTS,
            content=body.model_dump(),
        )
        response.headers["Retry-After"] = "60"
        return response


def _principal_key(request: Request) -> tuple[str, str]:
    """Идентификатор + тип принципала для метрики/логов."""
    auth = request.headers.get("authorization")
    if auth:
        digest = hashlib.sha256(auth.encode("utf-8")).hexdigest()[:16]
        return f"token:{digest}", "token"
    client = request.client
    if client is not None and client.host:
        return f"ip:{client.host}", "ip"
    return "anon", "anon"


__all__ = ["RateLimitMiddleware", "TokenBucketLimiter"]
