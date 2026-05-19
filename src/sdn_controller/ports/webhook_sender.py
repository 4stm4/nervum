"""Транспорт-порт для исходящих webhook-вызовов (SDN-054).

Реализации:
* ``HttpWebhookSender`` — реальный POST через ``httpx`` с timeout'ом
  и HMAC-подписью в заголовке;
* ``InMemoryWebhookSender`` — collector для тестов; пишет каждый
  вызов в список, плюс умеет имитировать failures по политике.

API намеренно узкое: подпись считает сам sender (так ему доступен
plaintext secret через ``signer``), отдавая dispatcher'у только
конечный результат.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WebhookDelivery:
    """Готовый payload для одной попытки доставки."""

    target_url: str
    event_id: int
    event_type: str
    resource_type: str
    resource_id: str | None
    body: bytes  # canonical JSON
    signature_header: str  # "sha256=<hex>"
    delivery_id: str  # короткий случайный id для логов/идемпотентности


@dataclass(frozen=True, slots=True)
class WebhookSendResult:
    ok: bool
    http_status: int | None
    error: str | None


class WebhookSender(Protocol):
    async def send(self, delivery: WebhookDelivery) -> WebhookSendResult: ...


__all__ = ["WebhookDelivery", "WebhookSendResult", "WebhookSender"]
