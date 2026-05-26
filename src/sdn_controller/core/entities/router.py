"""Сущность Router — L3-маршрутизатор (N3-01, N3-03, N3-04, N3-06).

Router агрегирует:
- ссылку на внешнюю сеть (uplink, NAT/masquerade),
- набор подключённых внутренних сетей,
- статические маршруты (destination → nexthop),
- опциональный IPv6-конфиг (SLAAC / DHCPv6),
- параметры HA через VRRP (N3-06).

Lifecycle: build → active | error → (при выключении) down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_address, ip_network
from typing import Any

from sdn_controller.core.value_objects.enums import (
    HaMode,
    Ipv6Mode,
    RouterStatus,
)
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import (
    NetworkId,
    ProjectId,
    RouterId,
)

_UNSET: Any = object()


@dataclass(frozen=True)
class StaticRoute:
    """Статический маршрут: пара (destination CIDR, nexthop IP)."""

    destination: str   # CIDR, например «0.0.0.0/0»
    nexthop: str       # IP-адрес следующего хопа

    def __post_init__(self) -> None:
        try:
            ip_network(self.destination, strict=False)
        except ValueError:
            raise ValidationError(f"некорректный CIDR маршрута: {self.destination!r}")
        try:
            ip_address(self.nexthop)
        except ValueError:
            raise ValidationError(f"некорректный nexthop: {self.nexthop!r}")


@dataclass(frozen=True)
class IPv6Config:
    """Конфигурация IPv6 на маршрутизаторе (N3-04).

    Используется RouterConfigurator для генерации radvd.conf /
    конфига DHCPv6-сервера.
    """

    mode: Ipv6Mode = Ipv6Mode.OFF
    prefix: str = ""           # IPv6-префикс для RA, например «2001:db8::/64»
    dhcpv6_stateful: bool = False  # True → IA-NA/IA-PD, False → только опции

    def __post_init__(self) -> None:
        if self.mode != Ipv6Mode.OFF and self.prefix:
            try:
                net = ip_network(self.prefix, strict=False)
                if net.version != 6:
                    raise ValidationError(f"prefix должен быть IPv6, получено: {self.prefix!r}")
            except ValueError:
                raise ValidationError(f"некорректный IPv6-префикс: {self.prefix!r}")


@dataclass
class Router:
    """L3-маршрутизатор — основная сущность N3 (N3-01, N3-03, N3-04, N3-06).

    ``external_network_id`` — uplink-сеть; трафик с внутренних сетей
    SNAT'ится в её адреса. None означает маршрутизатор без uplink'а.

    ``internal_network_ids`` — frozenset подключённых внутренних сетей.

    ``applied_config`` — последний сгенерированный конфиг (shell-скрипт),
    хранится для аудита и повторного применения.
    """

    id: RouterId
    name: str
    description: str = ""
    project_id: ProjectId | None = None
    external_network_id: NetworkId | None = None
    internal_network_ids: frozenset[NetworkId] = field(default_factory=frozenset)
    static_routes: tuple[StaticRoute, ...] = field(default_factory=tuple)
    status: RouterStatus = RouterStatus.BUILD
    admin_state_up: bool = True
    ha_mode: HaMode = HaMode.NONE
    vrrp_priority: int | None = None    # 1–254 (keepalived)
    vrrp_vrid: int | None = None        # 1–255 (VRRP Virtual Router ID)
    ipv6_config: IPv6Config | None = None
    applied_config: str | None = None
    applied_at: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        self._validate_vrrp()

    def _validate_vrrp(self) -> None:
        if self.ha_mode == HaMode.VRRP:
            if self.vrrp_vrid is not None and not (1 <= self.vrrp_vrid <= 255):
                raise ValidationError(
                    f"vrrp_vrid должен быть в диапазоне 1–255, получено {self.vrrp_vrid}"
                )
            if self.vrrp_priority is not None and not (1 <= self.vrrp_priority <= 254):
                raise ValidationError(
                    f"vrrp_priority должен быть в диапазоне 1–254, получено {self.vrrp_priority}"
                )

    def add_static_route(self, route: StaticRoute, *, now: datetime) -> None:
        """Добавляет статический маршрут; сбрасывает applied_config."""
        # Не допускаем дублирование destination
        existing = {r.destination for r in self.static_routes}
        if route.destination in existing:
            raise ValidationError(
                f"маршрут для {route.destination!r} уже существует"
            )
        self.static_routes = (*self.static_routes, route)
        self.applied_config = None
        self.updated_at = now

    def remove_static_route(self, destination: str, *, now: datetime) -> None:
        """Удаляет статический маршрут по destination CIDR."""
        new_routes = tuple(r for r in self.static_routes if r.destination != destination)
        if len(new_routes) == len(self.static_routes):
            raise ValidationError(f"маршрут для {destination!r} не найден")
        self.static_routes = new_routes
        self.applied_config = None
        self.updated_at = now

    def add_internal_network(self, network_id: NetworkId, *, now: datetime) -> None:
        """Подключает внутреннюю сеть к маршрутизатору."""
        self.internal_network_ids = frozenset({*self.internal_network_ids, network_id})
        self.applied_config = None
        self.updated_at = now

    def remove_internal_network(self, network_id: NetworkId, *, now: datetime) -> None:
        """Отключает внутреннюю сеть от маршрутизатора."""
        if network_id not in self.internal_network_ids:
            raise ValidationError(f"сеть {network_id!r} не подключена к маршрутизатору")
        self.internal_network_ids = self.internal_network_ids - {network_id}
        self.applied_config = None
        self.updated_at = now

    def mark_active(self, *, config: str, now: datetime) -> None:
        """Переводит маршрутизатор в статус active после успешного применения."""
        self.applied_config = config
        self.applied_at = now
        self.status = RouterStatus.ACTIVE
        self.updated_at = now

    def mark_error(self, *, now: datetime) -> None:
        """Переводит маршрутизатор в статус error."""
        self.status = RouterStatus.ERROR
        self.updated_at = now

    def set_admin_state(self, *, up: bool, now: datetime) -> None:
        """Административное включение/выключение."""
        self.admin_state_up = up
        self.status = RouterStatus.ACTIVE if up else RouterStatus.DOWN
        self.updated_at = now

    def update(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        external_network_id: Any = _UNSET,
        ha_mode: HaMode | None = None,
        vrrp_priority: Any = _UNSET,
        vrrp_vrid: Any = _UNSET,
        ipv6_config: Any = _UNSET,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        """Обновляет метаданные; сбрасывает applied_config при изменении топологии."""
        topology_changed = False
        if name is not None:
            self.name = name
        if description is not None:
            self.description = description
        if external_network_id is not _UNSET:
            self.external_network_id = external_network_id
            topology_changed = True
        if ha_mode is not None:
            self.ha_mode = ha_mode
            topology_changed = True
        if vrrp_priority is not _UNSET:
            self.vrrp_priority = vrrp_priority
            topology_changed = True
        if vrrp_vrid is not _UNSET:
            self.vrrp_vrid = vrrp_vrid
            topology_changed = True
        if ipv6_config is not _UNSET:
            self.ipv6_config = ipv6_config
            topology_changed = True
        if labels is not None:
            self.labels = labels
        if topology_changed:
            self.applied_config = None
            self._validate_vrrp()
        self.updated_at = now
