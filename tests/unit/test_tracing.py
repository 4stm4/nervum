"""Smoke-тесты ``configure_tracing`` + Settings-валидации (SDN-041).

OpenTelemetry хранит TracerProvider как **процесс-singleton**, и
``trace.set_tracer_provider`` ругается warning'ом, если её дёрнуть
повторно. Поэтому unit-тесты здесь намеренно консервативны: проверяют,
что ``configure_tracing`` не падает на разных конфигурациях и что
``Settings`` отвергает невалидные значения. «Реальная» отправка
span'ов в OTLP — out-of-scope для in-process юнит-тестов.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace

from sdn_controller.app.config import Settings
from sdn_controller.app.tracing import configure_tracing, reset_for_tests, tracer


def _base_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "persistence": "memory",
        "log_level": "WARNING",
        "log_format": "console",
        "auth_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset() -> None:
    """``_configured`` — process-global; сбрасываем перед каждым тестом."""
    reset_for_tests()


def test_disabled_tracing_does_not_install_provider() -> None:
    """При otel_enabled=False — никакого нового provider'а."""
    before = trace.get_tracer_provider()
    configure_tracing(_base_settings(otel_enabled=False))
    after = trace.get_tracer_provider()
    assert before is after


def test_tracer_is_always_available() -> None:
    """``tracer()`` отдаёт valid tracer даже без configure_tracing."""
    span = tracer().start_span("noop")
    span.end()  # не должно падать — NoOp ok


def test_configure_is_idempotent() -> None:
    settings = _base_settings(otel_enabled=False)
    configure_tracing(settings)
    configure_tracing(settings)  # второй вызов — no-op, не падает


def test_sample_rate_validation_low() -> None:
    with pytest.raises(ValueError, match="otel_sample_rate"):
        _base_settings(otel_sample_rate=-0.1)


def test_sample_rate_validation_high() -> None:
    with pytest.raises(ValueError, match="otel_sample_rate"):
        _base_settings(otel_sample_rate=1.1)


def test_sample_rate_accepts_zero() -> None:
    settings = _base_settings(otel_sample_rate=0.0)
    assert settings.otel_sample_rate == 0.0


def test_sample_rate_accepts_one() -> None:
    settings = _base_settings(otel_sample_rate=1.0)
    assert settings.otel_sample_rate == 1.0
