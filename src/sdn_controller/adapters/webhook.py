"""Webhook delivery adapters (SDN-054).

* ``SignerStore`` — кэш plaintext'ов секретов per-subscription. Plaintext
  виден ровно в момент Create-операции — мы кладём его в кэш, чтобы
  dispatcher мог считать HMAC. После рестарта процесса plaintext'ы
  теряются — disabled subscriptions заведены умышленно, оператор должен
  пересоздать secret через `POST /webhooks/{id}/rotate-secret` (M14).
  Полное хранение секретов уходит в M13-E (SDN-043 SecretStore).
* ``hmac_signature`` — детерминистичная подпись `sha256=<hex>`.
* ``HttpWebhookSender`` — httpx-based реальный sender. Лимит timeout'ом
  не даёт зависнуть на «тихих» подписчиках.
* ``InMemoryWebhookSender`` — collector для тестов.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field

import anyio
import httpx
import structlog

from sdn_controller.core.value_objects.ids import WebhookSubscriptionId
from sdn_controller.ports.webhook_sender import (
    WebhookDelivery,
    WebhookSendResult,
)

_log = structlog.get_logger(__name__)
_HTTP_OK = 200
_HTTP_REDIRECT = 300


def hmac_signature(*, secret_plaintext: str, body: bytes) -> str:
    digest = hmac.new(secret_plaintext.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def secret_hash(plaintext: str) -> str:
    """SHA-256 hex of the secret. Не используется для подписи — только
    для рассмотра «тот же ли это секрет» при rotate-secret."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class SignerStore:
    """In-memory кэш `subscription_id → plaintext`. Process-local."""

    def __init__(self) -> None:
        self._secrets: dict[WebhookSubscriptionId, str] = {}
        self._lock = anyio.Lock()

    async def remember(self, sub_id: WebhookSubscriptionId, plaintext: str) -> None:
        async with self._lock:
            self._secrets[sub_id] = plaintext

    async def get(self, sub_id: WebhookSubscriptionId) -> str | None:
        async with self._lock:
            return self._secrets.get(sub_id)

    async def forget(self, sub_id: WebhookSubscriptionId) -> None:
        async with self._lock:
            self._secrets.pop(sub_id, None)


class HttpWebhookSender:
    """HTTP-доставка webhook'а через ``httpx.AsyncClient``."""

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)

    async def send(self, delivery: WebhookDelivery) -> WebhookSendResult:
        headers = {
            "Content-Type": "application/json",
            "X-SDN-Event-Id": str(delivery.event_id),
            "X-SDN-Event-Type": delivery.event_type,
            "X-SDN-Delivery-Id": delivery.delivery_id,
            "X-SDN-Signature": delivery.signature_header,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    delivery.target_url, content=delivery.body, headers=headers
                )
            except httpx.HTTPError as exc:
                _log.info(
                    "webhook_transport_error",
                    target_url=delivery.target_url,
                    event_id=delivery.event_id,
                    error=str(exc),
                )
                return WebhookSendResult(ok=False, http_status=None, error=str(exc))
        ok = _HTTP_OK <= response.status_code < _HTTP_REDIRECT
        return WebhookSendResult(
            ok=ok,
            http_status=response.status_code,
            error=None if ok else f"http_{response.status_code}",
        )


@dataclass(slots=True)
class _RecordedCall:
    target_url: str
    event_id: int
    event_type: str
    body: bytes
    signature_header: str
    delivery_id: str


@dataclass(slots=True)
class InMemoryWebhookSender:
    """Тестовый sender: пишет вызовы в ``calls``; политикой fail_for_urls
    можно заставлять конкретные target'ы возвращать failure."""

    calls: list[_RecordedCall] = field(default_factory=list)
    fail_for_urls: set[str] = field(default_factory=set)
    fail_status: int = 502

    async def send(self, delivery: WebhookDelivery) -> WebhookSendResult:
        self.calls.append(
            _RecordedCall(
                target_url=delivery.target_url,
                event_id=delivery.event_id,
                event_type=delivery.event_type,
                body=delivery.body,
                signature_header=delivery.signature_header,
                delivery_id=delivery.delivery_id,
            )
        )
        if delivery.target_url in self.fail_for_urls:
            return WebhookSendResult(
                ok=False,
                http_status=self.fail_status,
                error=f"http_{self.fail_status}",
            )
        return WebhookSendResult(ok=True, http_status=200, error=None)


__all__ = [
    "HttpWebhookSender",
    "InMemoryWebhookSender",
    "SignerStore",
    "hmac_signature",
    "secret_hash",
]
