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

    # M9 — security.
    #
    # ``auth_enabled`` управляет всем входным auth-слоем northbound API.
    # В тестах его выключаем, в проде — обязательно True.
    #
    # ``auth_bootstrap_admin_token`` — plaintext одноразового админ-
    # токена, который контейнер при старте идемпотентно создаёт
    # (привязывая к service account ``bootstrap-admin``). Это
    # «второе ключевое отверстие»: первый оператор регистрирует
    # настоящие учётки и сразу отзывает bootstrap-токен.
    #
    # ``agent_mtls_*`` — параметры взаимного TLS между контроллером
    # и агентами. Когда выключено, контроллер ходит по HTTP без
    # клиентского сертификата; включенное состояние требует валидный
    # CA + клиентскую пару и проверяет pinned thumbprint узла.
    auth_enabled: bool = True
    auth_bootstrap_admin_token: str | None = None
    auth_bootstrap_admin_name: str = "bootstrap-admin"

    agent_mtls_enabled: bool = False
    agent_mtls_ca_cert_path: str | None = None
    agent_mtls_client_cert_path: str | None = None
    agent_mtls_client_key_path: str | None = None

    # M13 — background tasks. По умолчанию выключены: для масштабирования
    # оператор крутит N HTTP-реплик с false и одного worker'а с true.
    # Leader election в M15.
    background_tasks_enabled: bool = False
    reconciler_interval_seconds: int = Field(default=300, ge=10)
    reconciler_auto_apply: bool = False
    heartbeat_reaper_interval_seconds: int = Field(default=30, ge=5)

    # Retention. Если archive_backend=file, старые audit-события пишутся в
    # директорию построчным JSON Lines, а потом удаляются.
    retention_interval_seconds: int = Field(default=3600, ge=60)
    operation_retention_days: int = Field(default=90, ge=1)
    audit_retention_days: int = Field(default=365, ge=1)
    audit_archive_backend: Literal["noop", "file"] = "noop"
    audit_archive_directory: str | None = None

    # M13 — rate-limit (SDN-042). 0 → выключено. Лимит per-principal
    # token bucket'ом: capacity = N, refill = N / 60 sec. Идентификатор
    # principal'а берётся из ``Authorization``-токена; для unauth-режима
    # ключ degraded'ится до client IP. Превышение → 429.
    ratelimit_per_minute: int = Field(default=0, ge=0)

    # M13 — webhooks (SDN-054).
    # ``webhook_dispatch_interval_seconds`` — как часто dispatcher
    # читает outbox и доставляет события. ``max_failures`` — порог,
    # после которого подписка автоматически переводится в disabled.
    # ``request_timeout_seconds`` — timeout одного POST'а к подписчику.
    # ``batch_size`` — лимит событий за один tick per-subscription.
    webhook_dispatch_interval_seconds: int = Field(default=5, ge=1)
    webhook_max_failures: int = Field(default=10, ge=1)
    webhook_request_timeout_seconds: float = Field(default=5.0, gt=0)
    webhook_batch_size: int = Field(default=50, ge=1)
    # Для интеграционных тестов: использовать ``InMemoryWebhookSender``,
    # вместо реальной HTTP-доставки. Принимается только при memory-
    # persistence (для prod-сборки эта опция ignored).
    webhooks_use_inmemory_sender: bool = False

    # M13 — SecretStore (SDN-043).
    # ``memory`` — process-local dict (рестарт = подписки auto-disable).
    # ``file`` — Fernet-encrypted JSON-файл; обязательно задаётся
    # ``secret_store_key`` (Fernet, 32-byte url-safe base64). В prod
    # используется именно файловый бэкенд.
    secret_store_backend: Literal["memory", "file"] = "memory"  # noqa: S105 — enum literal, не пароль
    secret_store_path: str | None = None
    secret_store_key: str | None = None

    # M13 — HTTPS (SDN-036). По умолчанию выключено (dev слушает
    # plain HTTP, prod ставит за reverse proxy либо native uvicorn
    # TLS). ``tls_require_client_cert`` включает mTLS на northbound API
    # — отдельно от ``agent_mtls_*``, которые для южного канала.
    tls_enabled: bool = False
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    tls_ca_file: str | None = None
    tls_require_client_cert: bool = False


def load_settings() -> Settings:
    """Build the singleton ``Settings`` from the environment."""
    return Settings()
