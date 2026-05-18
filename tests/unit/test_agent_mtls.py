"""Unit-тесты ``build_mtls_ssl_context`` / ``compute_certificate_thumbprint``."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sdn_controller.adapters.netos_agent.mtls import (
    build_mtls_ssl_context,
    compute_certificate_thumbprint,
)
from sdn_controller.app.config import Settings
from sdn_controller.core.value_objects.errors import ValidationError


def test_thumbprint_is_sha256_of_der_bytes() -> None:
    der = b"\x30\x82fake-cert-bytes"
    expected = hashlib.sha256(der).hexdigest()
    assert compute_certificate_thumbprint(der) == expected
    assert len(compute_certificate_thumbprint(der)) == 64


def test_disabled_returns_none() -> None:
    settings = Settings(persistence="memory", agent_mtls_enabled=False)
    assert build_mtls_ssl_context(settings) is None


def test_enabled_without_paths_raises_validation() -> None:
    settings = Settings(persistence="memory", agent_mtls_enabled=True)
    with pytest.raises(ValidationError):
        build_mtls_ssl_context(settings)


def test_enabled_with_missing_files_raises_validation(tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    settings = Settings(
        persistence="memory",
        agent_mtls_enabled=True,
        agent_mtls_ca_cert_path=str(ca),
        agent_mtls_client_cert_path=str(tmp_path / "client.crt"),
        agent_mtls_client_key_path=str(tmp_path / "client.key"),
    )
    with pytest.raises(ValidationError):
        build_mtls_ssl_context(settings)
