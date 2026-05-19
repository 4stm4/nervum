"""Unit-тесты ``TokenBucketLimiter`` + integration smoke на 429 (SDN-042)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.http_api.rate_limit import TokenBucketLimiter
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.app.config import Settings
from sdn_controller.app.container import build_container
from tests.conftest import CountingIdFactory, FrozenClock, SequentialTokenFactory

# ---------------------------------------------------------------------------
# TokenBucketLimiter
# ---------------------------------------------------------------------------


async def test_limiter_initially_serves_up_to_capacity(clock: FrozenClock) -> None:
    limiter = TokenBucketLimiter(capacity=3.0, refill_rate_per_sec=0.05, clock=clock)
    assert await limiter.acquire("k") is True
    assert await limiter.acquire("k") is True
    assert await limiter.acquire("k") is True
    assert await limiter.acquire("k") is False


async def test_limiter_refills_proportionally(clock: FrozenClock) -> None:
    # capacity=2, refill=1 token/sec
    limiter = TokenBucketLimiter(capacity=2.0, refill_rate_per_sec=1.0, clock=clock)
    assert await limiter.acquire("k") is True
    assert await limiter.acquire("k") is True
    assert await limiter.acquire("k") is False  # bucket пуст

    clock.advance(1)  # +1 token
    assert await limiter.acquire("k") is True
    assert await limiter.acquire("k") is False


async def test_limiter_isolates_keys(clock: FrozenClock) -> None:
    limiter = TokenBucketLimiter(capacity=1.0, refill_rate_per_sec=0.0, clock=clock)
    assert await limiter.acquire("a") is True
    assert await limiter.acquire("b") is True
    assert await limiter.acquire("a") is False
    assert await limiter.acquire("b") is False


async def test_limiter_caps_refill_at_capacity(clock: FrozenClock) -> None:
    limiter = TokenBucketLimiter(capacity=2.0, refill_rate_per_sec=10.0, clock=clock)
    # Никогда не вызывали — bucket стартует full (capacity).
    assert await limiter.acquire("k") is True
    assert await limiter.acquire("k") is True
    assert await limiter.acquire("k") is False
    # Прыгнем далеко в будущее — bucket не должен накопить больше capacity.
    clock.advance(3600)
    assert await limiter.acquire("k") is True
    assert await limiter.acquire("k") is True
    assert await limiter.acquire("k") is False


# ---------------------------------------------------------------------------
# Integration: 429 через HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
async def aclient_with_limit(
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        persistence="memory",
        log_level="WARNING",
        log_format="console",
        auth_enabled=False,
        ratelimit_per_minute=2,
    )
    container = build_container(
        settings,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        agent=FakeAgent(clock=clock),
    )
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://controller") as http:
        yield http


async def test_rate_limit_returns_429_after_capacity(
    aclient_with_limit: httpx.AsyncClient,
) -> None:
    # capacity=2 → две первые проходят, третья — 429.
    r1 = await aclient_with_limit.get("/api/v1/livez")
    r2 = await aclient_with_limit.get("/api/v1/livez")
    r3 = await aclient_with_limit.get("/api/v1/livez")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429, r3.text
    body = r3.json()
    assert body["error"]["code"] == "rate_limited"
    assert r3.headers["Retry-After"] == "60"
