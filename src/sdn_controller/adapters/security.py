"""Production implementation of the security ports.

The default ``TokenFactory`` is backed by ``secrets`` from the stdlib. Tests
substitute a deterministic factory to keep assertions readable.
"""

from __future__ import annotations

from sdn_controller.core.entities import generate_token_plaintext


class SecretsTokenFactory:
    """Cryptographically strong token generation via ``secrets.token_urlsafe``."""

    def enrollment_token_plaintext(self) -> str:
        return generate_token_plaintext()
