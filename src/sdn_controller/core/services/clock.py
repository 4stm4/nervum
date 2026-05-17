"""Clock port.

Use cases never call ``datetime.now()`` directly — they take a ``Clock`` instead
so tests can assert exact timestamps without monkey-patching the stdlib.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current wall-clock time, always timezone-aware."""

    def now(self) -> datetime: ...


class SystemClock:
    """Default production clock — wraps ``datetime.now(UTC)``."""

    def now(self) -> datetime:
        return datetime.now(UTC)
