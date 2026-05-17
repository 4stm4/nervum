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

    # Persistence backend.
    # ``sqlite`` is the MVP default: a single file, no extra service to deploy.
    # ``memory`` exists for fast tests and short demos.
    # ``postgres`` will reuse the same SQLAlchemy adapter — only the URL and
    # the optional ``[postgres]`` extra (``asyncpg``) differ.
    persistence: Literal["memory", "sqlite", "postgres"] = "sqlite"
    database_url: str = "sqlite+aiosqlite:///./sdn_controller.db"
    database_echo: bool = False

    # Node enrollment / heartbeat tuning.
    #
    # ``enrollment_token_ttl_seconds`` — operator window between issuing a
    # token and the agent presenting it. One hour is enough for human-driven
    # provisioning yet short enough that a leaked token rarely outlives it.
    #
    # ``stale_after`` / ``offline_after`` define the effective node status
    # observed by readers (``ListNodes``/``GetNode``). Defaults assume agents
    # heartbeat every 30 s — three missed beats becomes ``stale``, ten missed
    # becomes ``offline``.
    enrollment_token_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    node_stale_after_seconds: int = Field(default=90, ge=10, le=3600)
    node_offline_after_seconds: int = Field(default=300, ge=30, le=86_400)


def load_settings() -> Settings:
    """Build the singleton ``Settings`` from the environment."""
    return Settings()
