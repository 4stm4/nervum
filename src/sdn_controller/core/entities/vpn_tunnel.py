"""Сущности VpnTunnel и VpnPeer — VPNaaS (N5-05).

``VpnTunnel`` — верхний уровень: параметры туннеля WireGuard или IPsec.
``VpnPeer``   — peer-запись внутри туннеля (публичный ключ + allowed IPs).

WireGuard-конфиг генерируется ``VpnConfigurator`` из этой пары сущностей.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.enums import VpnProtocol, VpnStatus
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import (
    ProjectId,
    VpnPeerId,
    VpnTunnelId,
)


@dataclass
class VpnTunnel:
    """VPN-туннель (WireGuard / IPsec) — N5-05.

    ``local_endpoint``   — IP-адрес или DNS локального хоста.
    ``remote_endpoint``  — IP-адрес или DNS удалённого хоста.
    ``listen_port``      — UDP-порт для WireGuard (по умолчанию 51820).
    ``local_public_key`` — публичный ключ локального интерфейса.
    ``remote_public_key``— публичный ключ удалённой стороны.
    ``preshared_key``    — необязательный pre-shared key (доп. уровень).
    ``applied_config``   — последний сгенерированный конфиг wg0.conf.
    """

    id: VpnTunnelId
    name: str
    protocol: VpnProtocol
    local_endpoint: str
    remote_endpoint: str
    local_public_key: str
    remote_public_key: str
    listen_port: int = 51820
    preshared_key: str | None = None
    project_id: ProjectId | None = None
    status: VpnStatus = VpnStatus.BUILD
    applied_config: str | None = None
    applied_at: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now())
    updated_at: datetime = field(default_factory=lambda: datetime.now())

    def __post_init__(self) -> None:
        if not (1 <= self.listen_port <= 65535):
            raise ValidationError(
                f"listen_port должен быть в диапазоне 1–65535, получено {self.listen_port}"
            )

    def apply(self, config: str, now: datetime) -> None:
        """Сохранить применённый конфиг."""
        self.applied_config = config
        self.applied_at = now
        self.status = VpnStatus.ACTIVE
        self.updated_at = now

    def set_down(self, now: datetime) -> None:
        """Перевести туннель в статус down."""
        self.status = VpnStatus.DOWN
        self.updated_at = now


@dataclass
class VpnPeer:
    """Peer-запись в туннеле WireGuard (N5-05).

    ``public_key``          — Curve25519-публичный ключ peer'а.
    ``endpoint``            — IP:port peer'а (None для роумингового клиента).
    ``allowed_ips``         — список CIDR-диапазонов, разрешённых через этот peer.
    ``persistent_keepalive``— интервал keepalive в секундах (0 = выключен).
    """

    id: VpnPeerId
    tunnel_id: VpnTunnelId
    public_key: str
    allowed_ips: list[str] = field(default_factory=list)
    endpoint: str | None = None
    persistent_keepalive: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now())
    updated_at: datetime = field(default_factory=lambda: datetime.now())

    def __post_init__(self) -> None:
        if self.persistent_keepalive < 0:
            raise ValidationError(
                f"persistent_keepalive не может быть отрицательным: {self.persistent_keepalive}"
            )
