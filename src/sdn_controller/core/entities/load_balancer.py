"""Сущности LBaaS: LoadBalancer, LbListener, LbPool, LbMember (N4-06).

Модель соответствует Octavia/OpenStack LBaaS v2:
  LoadBalancer → LbListener → LbPool → LbMember[].
LbConfigurator генерирует haproxy.cfg по этим сущностям.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_address

from sdn_controller.core.value_objects.enums import (
    LbAlgorithm,
    LbProtocol,
    LbStatus,
    SessionPersistence,
)
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import (
    LbListenerId,
    LbMemberId,
    LbPoolId,
    LoadBalancerId,
    NetworkId,
    ProjectId,
    RouterId,
)

_PORT_MIN = 1
_PORT_MAX = 65535


def _check_port(port: int, field_name: str = "port") -> None:
    if not (_PORT_MIN <= port <= _PORT_MAX):
        raise ValidationError(f"{field_name} должен быть {_PORT_MIN}–{_PORT_MAX}, получено {port}")


@dataclass
class LoadBalancer:
    """Виртуальный балансировщик нагрузки (N4-06).

    ``vip_address``      — виртуальный IP-адрес (VIP).
    ``vip_network_id``   — сеть, к которой принадлежит VIP.
    ``router_id``        — маршрутизатор для SNAT к бэкендам (опционально).
    ``provider``         — движок (haproxy, nginx, envoy).
    ``applied_config``   — последний сгенерированный конфиг.
    """

    id: LoadBalancerId
    name: str
    vip_address: str
    vip_network_id: NetworkId
    project_id: ProjectId | None = None
    router_id: RouterId | None = None
    description: str = ""
    provider: str = "haproxy"
    status: LbStatus = LbStatus.BUILD
    admin_state_up: bool = True
    applied_config: str | None = None
    applied_at: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        try:
            ip_address(self.vip_address)
        except ValueError:
            raise ValidationError(f"некорректный vip_address: {self.vip_address!r}")

    def mark_active(self, config: str, *, now: datetime) -> None:
        self.applied_config = config
        self.applied_at = now
        self.status = LbStatus.ACTIVE
        self.updated_at = now

    def mark_error(self, *, now: datetime) -> None:
        self.status = LbStatus.ERROR
        self.updated_at = now

    def set_admin_state(self, *, up: bool, now: datetime) -> None:
        self.admin_state_up = up
        self.status = LbStatus.ACTIVE if up else LbStatus.DOWN
        self.updated_at = now

    def update(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        if name is not None:
            self.name = name
        if description is not None:
            self.description = description
        if labels is not None:
            self.labels = labels
        self.updated_at = now


@dataclass
class LbListener:
    """Слушатель балансировщика — точка входа трафика (N4-06).

    ``protocol_port``    — порт, на котором балансировщик принимает запросы.
    ``default_pool_id``  — пул-умолчание (если нет правил маршрутизации).
    """

    id: LbListenerId
    name: str
    lb_id: LoadBalancerId
    protocol: LbProtocol
    protocol_port: int
    default_pool_id: LbPoolId | None = None
    description: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        _check_port(self.protocol_port, "protocol_port")

    def update(
        self,
        *,
        name: str | None = None,
        default_pool_id: LbPoolId | None = None,
        description: str | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        if name is not None:
            self.name = name
        if default_pool_id is not None:
            self.default_pool_id = default_pool_id
        if description is not None:
            self.description = description
        if labels is not None:
            self.labels = labels
        self.updated_at = now


@dataclass
class LbPool:
    """Пул бэкендов балансировщика (N4-06).

    ``lb_algorithm``        — алгоритм распределения запросов.
    ``session_persistence`` — режим Session Persistence.
    """

    id: LbPoolId
    name: str
    lb_id: LoadBalancerId
    protocol: LbProtocol
    lb_algorithm: LbAlgorithm = LbAlgorithm.ROUND_ROBIN
    session_persistence: SessionPersistence = SessionPersistence.NONE
    description: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def update(
        self,
        *,
        name: str | None = None,
        lb_algorithm: LbAlgorithm | None = None,
        session_persistence: SessionPersistence | None = None,
        description: str | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        if name is not None:
            self.name = name
        if lb_algorithm is not None:
            self.lb_algorithm = lb_algorithm
        if session_persistence is not None:
            self.session_persistence = session_persistence
        if description is not None:
            self.description = description
        if labels is not None:
            self.labels = labels
        self.updated_at = now


@dataclass
class LbMember:
    """Участник пула балансировщика — отдельный бэкенд (N4-06).

    ``address``       — IP-адрес бэкенда.
    ``protocol_port`` — порт бэкенда.
    ``weight``        — вес при распределении нагрузки (1–256).
    """

    id: LbMemberId
    pool_id: LbPoolId
    address: str
    protocol_port: int
    weight: int = 1
    admin_state_up: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        try:
            ip_address(self.address)
        except ValueError:
            raise ValidationError(f"некорректный адрес бэкенда: {self.address!r}")
        _check_port(self.protocol_port, "protocol_port")
        if not (1 <= self.weight <= 256):
            raise ValidationError(f"weight должен быть 1–256, получено {self.weight}")

    def update(
        self,
        *,
        weight: int | None = None,
        admin_state_up: bool | None = None,
        now: datetime,
    ) -> None:
        if weight is not None:
            if not (1 <= weight <= 256):
                raise ValidationError(f"weight должен быть 1–256, получено {weight}")
            self.weight = weight
        if admin_state_up is not None:
            self.admin_state_up = admin_state_up
        self.updated_at = now
