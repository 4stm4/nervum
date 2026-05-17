"""Runtime configuration.

All values come from environment variables with the ``SDN_`` prefix, e.g.
``SDN_HTTP_PORT=9000``. Defaults are safe for local development.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level settings object. Subgroups are nested for clarity."""

    model_config = SettingsConfigDict(
        env_prefix="SDN_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["dev", "staging", "prod"] = "dev"
    http_host: str = "0.0.0.0"  # noqa: S104 — operator decides bind via env
    http_port: int = Field(default=8080, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Persistence backend. ``memory`` is the milestone-1 default; ``postgres``
    # is wired up in SDN-002.
    persistence: Literal["memory", "postgres"] = "memory"
    database_url: str | None = None


def load_settings() -> Settings:
    """Build the singleton ``Settings`` from the environment."""
    return Settings()
