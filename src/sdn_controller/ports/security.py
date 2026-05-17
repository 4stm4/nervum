"""Security ports — token generation today, mTLS cert issuance later (M9).

We isolate randomness behind a port so tests can substitute a deterministic
generator and so a future hardware-backed RNG (e.g. KMS) can be slotted in
without touching use cases.
"""

from __future__ import annotations

from typing import Protocol


class TokenFactory(Protocol):
    """Produces fresh, high-entropy enrolment tokens.

    The plaintext is a self-describing string (``enroll_<base64url>``); the
    consumer immediately hashes it for storage and shows the plaintext to the
    operator exactly once.
    """

    def enrollment_token_plaintext(self) -> str: ...
