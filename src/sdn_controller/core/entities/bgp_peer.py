"""Сущность BgpPeer — BGP-пир маршрутизатора (N3-05).

Хранит конфигурацию одного BGP-пира (neighbor) для заданного маршрутизатора.
Реальное состояние сессии (established/idle/...) запрашивается у агента при
verify; ``state`` в сущности — последнее известное контроллеру состояние.

BgpConfigurator использует набор пиров маршрутизатора для генерации
bird.conf / frr.conf.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_address

from sdn_controller.core.value_objects.enums import BgpPeerState
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import (
    BgpPeerId,
    ProjectId,
    RouterId,
)

_ASN_MIN = 1
_ASN_MAX = 4_294_967_295  # 32-bit ASN (RFC 6793)


def _validate_asn(asn: int, field_name: str) -> None:
    if not (_ASN_MIN <= asn <= _ASN_MAX):
        raise ValidationError(
            f"{field_name} должен быть в диапазоне {_ASN_MIN}–{_ASN_MAX}, получено {asn}"
        )


@dataclass
class BgpPeer:
    """BGP-пир маршрутизатора (N3-05).

    ``peer_ip``  — IP-адрес удалённого пира.
    ``peer_asn`` — ASN удалённого пира.
    ``local_asn``— локальный ASN маршрутизатора.
    ``password`` — MD5-пароль аутентификации (пустая строка = нет аутентификации).
    ``state``    — последнее известное состояние BGP-сессии (обновляется верификатором).
    """

    id: BgpPeerId
    router_id: RouterId
    peer_ip: str
    peer_asn: int
    local_asn: int
    password: str = ""
    state: BgpPeerState = BgpPeerState.IDLE
    project_id: ProjectId | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        try:
            ip_address(self.peer_ip)
        except ValueError:
            raise ValidationError(f"некорректный peer_ip: {self.peer_ip!r}")
        _validate_asn(self.peer_asn, "peer_asn")
        _validate_asn(self.local_asn, "local_asn")

    def update_state(self, state: BgpPeerState, *, now: datetime) -> None:
        """Обновляет состояние BGP-сессии (вызывается верификатором)."""
        self.state = state
        self.updated_at = now

    def update(
        self,
        *,
        password: str | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        if password is not None:
            self.password = password
        if labels is not None:
            self.labels = labels
        self.updated_at = now
