"""Use cases для background-tasks (M13 — SDN-038, SDN-040).

Эти штуки **не** дёргаются HTTP-ручками; их запускает контейнер в
lifespan'е (одну реплику с ``SDN_BACKGROUND_TASKS_ENABLED=true``).
Каждая task'а спроектирована так, чтобы один проход был атомарным
шагом: запустилась, сделала работу, вернулась. Дальше container
крутит её циклом с интервалом.

* ``ReconcilerSweep`` — обходит все сети, считает drift через
  ``ScanDrift``, метрики экспонирует наружу. Если включён
  ``auto_apply`` — затрагивает только сети, у которых drift не
  пустой.
* ``HeartbeatReaper`` — переводит узлы в ``stale``/``offline`` без
  lazy-derive в getter'ах ``ListNodes``/``GetNode``.
* ``RetentionSweep`` — удаляет терминальные operations старше
  ``retention_days`` и архивирует/удаляет audit-события старше
  ``audit_retention_days``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog

from sdn_controller.core.entities import Node
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.services.node_status import derived_status
from sdn_controller.core.use_cases.reconcile import ApplyNetwork
from sdn_controller.core.use_cases.topology import ScanDrift
from sdn_controller.core.value_objects.enums import NodeStatus
from sdn_controller.core.value_objects.errors import DomainError
from sdn_controller.core.value_objects.ids import NetworkId
from sdn_controller.ports.audit_archive import AuditArchive
from sdn_controller.ports.persistence import (
    AuditEventRepository,
    NetworkRepository,
    NodeRepository,
    OperationRepository,
)

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# ReconcilerSweep (SDN-038)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReconcilerSweepResult:
    networks_total: int
    networks_drifting: int
    stale_nodes: int
    auto_applied: int


class ReconcilerSweep:
    def __init__(
        self,
        *,
        scan_drift: ScanDrift,
        networks: NetworkRepository,
        apply_network: ApplyNetwork,
        auto_apply: bool,
    ) -> None:
        self._scan = scan_drift
        self._networks = networks
        self._apply = apply_network
        self._auto = auto_apply

    async def execute(self) -> ReconcilerSweepResult:
        report = await self._scan.execute()
        all_networks = await self._networks.list()
        networks_total = len(all_networks)
        drifting: set[str] = {item.network_id for item in report.items}
        auto_applied = 0
        if self._auto and drifting:
            for net_id in drifting:
                try:
                    await self._apply.execute(
                        NetworkId(net_id),
                        requested_by="reconciler:auto",
                    )
                    auto_applied += 1
                except DomainError as exc:
                    _log.warning(
                        "reconciler_auto_apply_failed",
                        network_id=net_id,
                        error=str(exc),
                    )
        return ReconcilerSweepResult(
            networks_total=networks_total,
            networks_drifting=len(drifting),
            stale_nodes=len(report.stale_nodes),
            auto_applied=auto_applied,
        )


# ---------------------------------------------------------------------------
# HeartbeatReaper (SDN-038)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeartbeatReaperResult:
    online: int
    stale: int
    offline: int


class HeartbeatReaper:
    """Прокидывает derived-status в постоянное хранилище.

    Сейчас ``ListNodes``/``GetNode`` уже вычисляют ``stale``/``offline``
    на лету через ``derived_status``. Reaper переводит это в персистент,
    чтобы тяжёлые читатели (UI, dashboards, external-orchestrator
    через testum) видели одинаковый статус, не вызывая бизнес-логику
    у каждой реплики.
    """

    def __init__(
        self,
        *,
        nodes: NodeRepository,
        clock: Clock,
        stale_after_seconds: int,
        offline_after_seconds: int,
    ) -> None:
        self._nodes = nodes
        self._clock = clock
        self._stale_after = stale_after_seconds
        self._offline_after = offline_after_seconds

    async def execute(self) -> HeartbeatReaperResult:
        now = self._clock.now()
        counts = {NodeStatus.ONLINE: 0, NodeStatus.STALE: 0, NodeStatus.OFFLINE: 0}
        for node in await self._nodes.list():
            new_status = derived_status(
                node,
                now=now,
                stale_after_seconds=self._stale_after,
                offline_after_seconds=self._offline_after,
            )
            if new_status in counts:
                counts[new_status] += 1
            # Не трогаем ``pending``/``draining`` — они управляются явно.
            if (
                new_status is not node.status
                and _is_heartbeat_status(new_status)
                and node.status in (NodeStatus.ONLINE, NodeStatus.STALE, NodeStatus.OFFLINE)
            ):
                self._persist_status(node, new_status, now)
                await self._nodes.save(node)
        return HeartbeatReaperResult(
            online=counts[NodeStatus.ONLINE],
            stale=counts[NodeStatus.STALE],
            offline=counts[NodeStatus.OFFLINE],
        )

    @staticmethod
    def _persist_status(node: Node, status: NodeStatus, now: datetime) -> None:
        node.status = status
        node.updated_at = now


def _is_heartbeat_status(status: NodeStatus) -> bool:
    return status in (NodeStatus.ONLINE, NodeStatus.STALE, NodeStatus.OFFLINE)


# ---------------------------------------------------------------------------
# RetentionSweep (SDN-040)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetentionSweepResult:
    operations_deleted: int
    audit_deleted: int
    audit_archived: int


class RetentionSweep:
    def __init__(
        self,
        *,
        operations: OperationRepository,
        audit_events: AuditEventRepository,
        audit_archive: AuditArchive,
        clock: Clock,
        operation_retention_days: int,
        audit_retention_days: int,
    ) -> None:
        self._operations = operations
        self._audit = audit_events
        self._archive = audit_archive
        self._clock = clock
        self._op_days = operation_retention_days
        self._audit_days = audit_retention_days

    async def execute(self) -> RetentionSweepResult:
        now = self._clock.now()
        op_cutoff = now - timedelta(days=self._op_days)
        audit_cutoff = now - timedelta(days=self._audit_days)

        ops_deleted = await self._operations.delete_terminal_before(op_cutoff)

        # Batch-цикл: вычитываем по 1000, отправляем в архив, удаляем
        # ровно ту же партию (чтобы не потерять часть при limit'е).
        # Когда ``list_before`` вернул пусто — выходим.
        audit_archived = 0
        audit_deleted = 0
        while True:
            batch = await self._audit.list_before(audit_cutoff, limit=1000)
            if not batch:
                break
            await self._archive.archive(batch)
            audit_archived += len(batch)
            audit_deleted += await self._audit.delete_many([e.id for e in batch])

        return RetentionSweepResult(
            operations_deleted=ops_deleted,
            audit_deleted=audit_deleted,
            audit_archived=audit_archived,
        )


__all__ = [
    "HeartbeatReaper",
    "HeartbeatReaperResult",
    "ReconcilerSweep",
    "ReconcilerSweepResult",
    "RetentionSweep",
    "RetentionSweepResult",
]
