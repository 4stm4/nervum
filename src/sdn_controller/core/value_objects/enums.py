"""Domain enums.

Enums are part of the public domain vocabulary — they must remain stable across
adapters, so we centralise them here and never reach for adapter-specific
literals (e.g. SQL strings, JSON labels) inside the core.
"""

from __future__ import annotations

from enum import StrEnum


class NetworkType(StrEnum):
    """Layer-2 network segment type."""

    FLAT = "flat"
    VLAN = "vlan"
    VXLAN = "vxlan"


class NodeStatus(StrEnum):
    """Lifecycle of a managed node as seen by the controller.

    ``pending``  — enrolment token issued, agent has not yet contacted us.
    ``online``   — recent heartbeat, agent ready.
    ``stale``    — last heartbeat older than the stale threshold.
    ``offline``  — last heartbeat older than the offline threshold.
    ``draining`` — node is being decommissioned, do not schedule new workloads.
    """

    PENDING = "pending"
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"
    DRAINING = "draining"


class OperationStatus(StrEnum):
    """Async operation lifecycle.

    State machine (terminal states marked ``*``)::

        accepted → planning → running → verifying → succeeded*
                                                 ↘ failed*
                                                 ↘ rolled_back*
                                     ↘ cancelled*
    """

    ACCEPTED = "accepted"
    PLANNING = "planning"
    RUNNING = "running"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_OPERATION_STATES


_TERMINAL_OPERATION_STATES: frozenset[OperationStatus] = frozenset(
    {
        OperationStatus.SUCCEEDED,
        OperationStatus.FAILED,
        OperationStatus.CANCELLED,
        OperationStatus.ROLLED_BACK,
    }
)


class OperationKind(StrEnum):
    """What domain action an operation represents."""

    NETWORK_CREATE = "network.create"
    NETWORK_UPDATE = "network.update"
    NETWORK_DELETE = "network.delete"
    NETWORK_APPLY = "network.apply"
    NODE_ENROLL = "node.enroll"
    NODE_REMOVE = "node.remove"
    DRIFT_SCAN = "drift.scan"


class WebhookSubscriptionState(StrEnum):
    """Webhook subscription lifecycle (SDN-054)."""

    ACTIVE = "active"
    DISABLED = "disabled"


class LogicalPortStatus(StrEnum):
    """Lifecycle of a logical port (N1-01).

    ``pending``  — создан, ещё не прикреплён к VIF.
    ``active``   — прикреплён и пропускает трафик.
    ``detached`` — VIF удалён; порт сохранён для аудита, но не активен.
    """

    PENDING = "pending"
    ACTIVE = "active"
    DETACHED = "detached"


class SecurityPolicyStatus(StrEnum):
    """Жизненный цикл политики безопасности (N2-01, N2-03).

    ``draft``     — создана или изменена, компиляция не выполнялась.
    ``compiled``  — ruleset скомпилирован, но ещё не применён.
    ``applied``   — ruleset отправлен на узлы и подтверждён.
    ``failed``    — применение завершилось ошибкой.
    """

    DRAFT = "draft"
    COMPILED = "compiled"
    APPLIED = "applied"
    FAILED = "failed"


class RouterStatus(StrEnum):
    """Жизненный цикл L3-маршрутизатора (N3-01, N3-03).

    ``build``   — создан, конфигурация не применялась.
    ``active``  — конфигурация применена, маршрутизатор работает.
    ``down``    — административно выключен (admin_state_up=False).
    ``error``   — применение завершилось ошибкой.
    """

    BUILD = "build"
    ACTIVE = "active"
    DOWN = "down"
    ERROR = "error"


class FloatingIpStatus(StrEnum):
    """Статус Floating IP (N3-02).

    ``down``   — выделен, но не ассоциирован с портом.
    ``active`` — ассоциирован с логическим портом и работает.
    ``error``  — ассоциация завершилась ошибкой.
    """

    DOWN = "down"
    ACTIVE = "active"
    ERROR = "error"


class HaMode(StrEnum):
    """Режим высокой доступности маршрутизатора (N3-06).

    ``none`` — одиночный маршрутизатор без резервирования.
    ``vrrp`` — активный/резервный через VRRP (keepalived).
    """

    NONE = "none"
    VRRP = "vrrp"


class BgpPeerState(StrEnum):
    """Состояние BGP-сессии согласно RFC 4271 (N3-05).

    Хранится в сущности как последнее известное состояние;
    реальное состояние запрашивается у агента через verify.
    """

    IDLE = "idle"
    CONNECT = "connect"
    ACTIVE = "active"
    OPENSENT = "opensent"
    OPENCONFIRM = "openconfirm"
    ESTABLISHED = "established"


class Ipv6Mode(StrEnum):
    """Режим IPv6-адресации на маршрутизаторе (N3-04).

    ``off``       — IPv6 отключён.
    ``slaac``     — Stateless Address Autoconfiguration (RA).
    ``stateful``  — DHCPv6 stateful (ia-na/ia-pd).
    ``stateless`` — DHCPv6 stateless (только опции, без адресов).
    """

    OFF = "off"
    SLAAC = "slaac"
    STATEFUL = "stateful"
    STATELESS = "stateless"


# ---------------------------------------------------------------------------
# N4 enums
# ---------------------------------------------------------------------------


class QuotaResource(StrEnum):
    """Типы ресурсов, на которые распространяются квоты (N4-01)."""

    NETWORKS = "networks"
    ROUTERS = "routers"
    FLOATING_IPS = "floating_ips"
    LOGICAL_PORTS = "logical_ports"
    SECURITY_GROUPS = "security_groups"
    LOAD_BALANCERS = "load_balancers"
    SNAPSHOTS = "snapshots"


class LbAlgorithm(StrEnum):
    """Алгоритм балансировки нагрузки (N4-06)."""

    ROUND_ROBIN = "round_robin"
    LEAST_CONNECTIONS = "least_connections"
    SOURCE_IP = "source_ip"


class LbProtocol(StrEnum):
    """Протокол балансировщика нагрузки (N4-06)."""

    HTTP = "http"
    HTTPS = "https"
    TCP = "tcp"
    UDP = "udp"


class LbStatus(StrEnum):
    """Жизненный цикл балансировщика нагрузки (N4-06).

    ``build``   — создан, конфиг не применён.
    ``active``  — конфиг применён, LB работает.
    ``down``    — административно выключен.
    ``error``   — применение завершилось ошибкой.
    """

    BUILD = "build"
    ACTIVE = "active"
    DOWN = "down"
    ERROR = "error"


class SessionPersistence(StrEnum):
    """Режим session persistence для пула (N4-06)."""

    NONE = "none"
    SOURCE_IP = "source_ip"
    HTTP_COOKIE = "http_cookie"
    APP_COOKIE = "app_cookie"


class HealthCheckType(StrEnum):
    """Тип health-check для бэкендов (N4-07)."""

    HTTP = "http"
    HTTPS = "https"
    TCP = "tcp"
    PING = "ping"


class BondMode(StrEnum):
    """Режим агрегации каналов (N4-04).

    ``none``           — нет агрегации.
    ``active_backup``  — один активный, остальные резервные.
    ``lacp``           — IEEE 802.3ad LACP.
    """

    NONE = "none"
    ACTIVE_BACKUP = "active_backup"
    LACP = "lacp"


class RetentionScope(StrEnum):
    """Область применения политики хранения (N4-05)."""

    AUDIT_EVENTS = "audit_events"
    OPERATIONS = "operations"
    OUTBOX_EVENTS = "outbox_events"
    SNAPSHOTS = "snapshots"
    ALL = "all"
