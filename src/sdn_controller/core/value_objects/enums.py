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
