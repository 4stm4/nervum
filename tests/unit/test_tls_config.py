"""Unit-тесты для ``_tls_kwargs`` (SDN-036)."""

from __future__ import annotations

import ssl

import pytest

from sdn_controller.app.config import Settings
from sdn_controller.app.main import _tls_kwargs


def _base_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "persistence": "memory",
        "log_level": "WARNING",
        "log_format": "console",
        "auth_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_tls_disabled_returns_empty() -> None:
    settings = _base_settings(tls_enabled=False)
    assert _tls_kwargs(settings) == {}


def test_tls_enabled_requires_cert_and_key() -> None:
    settings = _base_settings(tls_enabled=True)
    with pytest.raises(RuntimeError, match="SDN_TLS_CERT_FILE"):
        _tls_kwargs(settings)


def test_tls_enabled_basic_kwargs() -> None:
    settings = _base_settings(
        tls_enabled=True,
        tls_cert_file="/etc/sdn/cert.pem",
        tls_key_file="/etc/sdn/key.pem",
    )
    kwargs = _tls_kwargs(settings)
    assert kwargs == {
        "ssl_certfile": "/etc/sdn/cert.pem",
        "ssl_keyfile": "/etc/sdn/key.pem",
    }


def test_tls_with_ca_file() -> None:
    settings = _base_settings(
        tls_enabled=True,
        tls_cert_file="/etc/sdn/cert.pem",
        tls_key_file="/etc/sdn/key.pem",
        tls_ca_file="/etc/sdn/ca.pem",
    )
    kwargs = _tls_kwargs(settings)
    assert kwargs["ssl_ca_certs"] == "/etc/sdn/ca.pem"
    assert "ssl_cert_reqs" not in kwargs


def test_mtls_requires_ca_file() -> None:
    settings = _base_settings(
        tls_enabled=True,
        tls_cert_file="/etc/sdn/cert.pem",
        tls_key_file="/etc/sdn/key.pem",
        tls_require_client_cert=True,
    )
    with pytest.raises(RuntimeError, match="SDN_TLS_CA_FILE"):
        _tls_kwargs(settings)


def test_mtls_sets_required_cert_reqs() -> None:
    settings = _base_settings(
        tls_enabled=True,
        tls_cert_file="/etc/sdn/cert.pem",
        tls_key_file="/etc/sdn/key.pem",
        tls_ca_file="/etc/sdn/ca.pem",
        tls_require_client_cert=True,
    )
    kwargs = _tls_kwargs(settings)
    assert kwargs["ssl_cert_reqs"] == ssl.CERT_REQUIRED
