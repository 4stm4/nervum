"""Webhook subscription aggregate (SDN-054).

A subscription is a stable endpoint that the controller will POST events
to, plus the bookkeeping the dispatcher needs to be at-least-once and
idempotent:

* ``target_url`` — куда стучимся;
* ``secret_hash`` — sha256(plaintext), для HMAC-подписи в исходящих
  webhook'ах. Plaintext возвращается из ``Create`` ровно один раз;
* ``event_types`` — фильтр; ``("*",)`` подписывает на всё;
* ``cursor`` — watermark: dispatcher не отдаст событие с ``event_id <=
  cursor``. Это и есть «memory» доставки: продолжаем с того места,
  где остановились после рестарта;
* ``state`` — ``active`` / ``disabled``. ``disabled`` подписки
  dispatcher пропускает — это итог либо ручного выключения, либо
  автоматического "вырубила сама себя после N подряд failures".

Подпись (для подписчика):

    X-SDN-Signature: sha256=<HMAC-SHA256(secret_plaintext, body_bytes)>

Контракт не позволяет dispatcher'у знать plaintext после `Create` —
поэтому secret_hash и подписывание идут через ``SignerStore`` (in-mem
кэш plaintext'ов, разогретый при создании). Это компромисс, который
устранится в E-блоке (SDN-043 SecretStore).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.enums import WebhookSubscriptionState
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import WebhookSubscriptionId


@dataclass(slots=True)
class WebhookSubscription:
    id: WebhookSubscriptionId
    target_url: str
    secret_hash: str
    event_types: tuple[str, ...]
    state: WebhookSubscriptionState
    created_at: datetime
    updated_at: datetime
    cursor: int = 0
    last_delivery_at: datetime | None = None
    last_delivery_status: str | None = None
    failure_count: int = 0
    description: str | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.target_url.startswith(("http://", "https://")):
            raise ValidationError(
                f"webhook target_url must start with http(s)://: {self.target_url!r}",
            )
        if not self.event_types:
            raise ValidationError("webhook event_types must be non-empty")
        for et in self.event_types:
            if et != "*" and "." not in et:
                raise ValidationError(
                    f"event_type must be '*' or '<resource>.<verb>': {et!r}",
                )
        if self.cursor < 0:
            raise ValidationError("webhook cursor must be >= 0")
        if self.failure_count < 0:
            raise ValidationError("webhook failure_count must be >= 0")

    def matches(self, event_type: str) -> bool:
        return "*" in self.event_types or event_type in self.event_types

    def mark_delivered(self, *, event_id: int, at: datetime) -> None:
        if event_id <= self.cursor:
            return
        self.cursor = event_id
        self.last_delivery_at = at
        self.last_delivery_status = "ok"
        self.failure_count = 0
        self.updated_at = at

    def mark_failed(self, *, at: datetime, error: str) -> None:
        self.failure_count += 1
        self.last_delivery_at = at
        self.last_delivery_status = f"error: {error}"[:255]
        self.updated_at = at

    def disable(self, *, at: datetime, reason: str) -> None:
        self.state = WebhookSubscriptionState.DISABLED
        self.last_delivery_status = f"disabled: {reason}"[:255]
        self.updated_at = at


__all__ = ["WebhookSubscription"]
