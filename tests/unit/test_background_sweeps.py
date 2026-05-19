"""Unit-тесты для ``ReconcilerSweep``/``HeartbeatReaper``/``RetentionSweep``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sdn_controller.adapters.audit_archive import FileAuditArchive, NoopAuditArchive
from sdn_controller.adapters.locks import InMemoryLockStore
from sdn_controller.adapters.memory import (
    InMemoryAuditEventRepository,
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryObservedStateRepository,
    InMemoryOperationRepository,
)
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.core.entities import (
    AuditEvent,
    Network,
    Node,
    ObservedState,
    Operation,
    ResourceRef,
)
from sdn_controller.core.services.planner import Planner
from sdn_controller.core.use_cases.background import (
    HeartbeatReaper,
    ReconcilerSweep,
    RetentionSweep,
)
from sdn_controller.core.use_cases.reconcile import ApplyNetwork
from sdn_controller.core.use_cases.topology import ScanDrift
from sdn_controller.core.value_objects.enums import (
    NetworkType,
    NodeStatus,
    OperationKind,
    OperationStatus,
)
from sdn_controller.core.value_objects.ids import AuditEventId, NetworkId, NodeId, OperationId
from tests.conftest import CountingIdFactory, FrozenClock

_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


def _node(name: str, ip: str = "10.0.0.1", **overrides: object) -> Node:
    base: dict[str, object] = {
        "id": NodeId(name),
        "name": name,
        "mgmt_ip": ip,
        "status": NodeStatus.ONLINE,
        "created_at": _NOW,
        "updated_at": _NOW,
        "last_seen_at": _NOW,
    }
    base.update(overrides)
    return Node(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HeartbeatReaper
# ---------------------------------------------------------------------------


async def test_reaper_moves_online_to_stale_after_threshold(
    clock: FrozenClock,
) -> None:
    nodes = InMemoryNodeRepository()
    await nodes.save(_node("a", last_seen_at=clock.current))
    clock.advance(120)  # > stale threshold (90s)

    reaper = HeartbeatReaper(
        nodes=nodes,
        clock=clock,
        stale_after_seconds=90,
        offline_after_seconds=300,
    )
    result = await reaper.execute()
    assert result.stale == 1
    persisted = await nodes.get(NodeId("a"))
    assert persisted is not None
    assert persisted.status is NodeStatus.STALE


async def test_reaper_does_not_touch_pending(clock: FrozenClock) -> None:
    nodes = InMemoryNodeRepository()
    await nodes.save(_node("a", status=NodeStatus.PENDING, last_seen_at=None))
    clock.advance(600)

    reaper = HeartbeatReaper(
        nodes=nodes, clock=clock, stale_after_seconds=90, offline_after_seconds=300
    )
    await reaper.execute()
    persisted = await nodes.get(NodeId("a"))
    assert persisted is not None
    assert persisted.status is NodeStatus.PENDING


# ---------------------------------------------------------------------------
# ReconcilerSweep
# ---------------------------------------------------------------------------


def _network(name: str) -> Network:
    return Network(
        id=NetworkId(f"net_{name}"),
        name=name,
        type=NetworkType.VXLAN,
        vni=10100,
        created_at=_NOW,
        updated_at=_NOW,
    )


async def test_reconciler_sees_no_drift_when_no_networks(
    clock: FrozenClock,
    ids: CountingIdFactory,
    fake_agent: FakeAgent,
) -> None:
    networks = InMemoryNetworkRepository()
    nodes = InMemoryNodeRepository()
    observed = InMemoryObservedStateRepository()
    operations = InMemoryOperationRepository()
    sweep = ReconcilerSweep(
        scan_drift=ScanDrift(nodes=nodes, networks=networks, observed_states=observed, clock=clock),
        networks=networks,
        apply_network=ApplyNetwork(
            networks=networks,
            nodes=nodes,
            observed_states=observed,
            operations=operations,
            planner=Planner(ids=ids),
            agent=fake_agent,
            clock=clock,
            ids=ids,
            locks=InMemoryLockStore(clock=clock),
        ),
        auto_apply=False,
    )
    result = await sweep.execute()
    assert result.networks_total == 0
    assert result.networks_drifting == 0
    assert result.auto_applied == 0


async def test_reconciler_auto_apply_runs_for_drifting_networks(
    clock: FrozenClock,
    ids: CountingIdFactory,
    fake_agent: FakeAgent,
) -> None:
    networks = InMemoryNetworkRepository()
    nodes = InMemoryNodeRepository()
    observed = InMemoryObservedStateRepository()
    operations = InMemoryOperationRepository()

    node = _node("node_a", "10.0.0.1")
    await nodes.save(node)
    net = _network("prod")
    net.set_nodes((NodeId("node_a"),), now=_NOW)
    await networks.save(net)
    # observed state не сохранён → stale_nodes; ScanDrift его пропустит,
    # но всё равно сеть сама без observed = stale (drift пуст, узел stale).
    # Чтобы спровоцировать реальный drift, сохраним пустой observed —
    # тогда diff_for_node увидит missing bridge.
    await observed.save(ObservedState(node_id=NodeId("node_a"), observed_at=_NOW, state_hash="0"))

    sweep = ReconcilerSweep(
        scan_drift=ScanDrift(nodes=nodes, networks=networks, observed_states=observed, clock=clock),
        networks=networks,
        apply_network=ApplyNetwork(
            networks=networks,
            nodes=nodes,
            observed_states=observed,
            operations=operations,
            planner=Planner(ids=ids),
            agent=fake_agent,
            clock=clock,
            ids=ids,
            locks=InMemoryLockStore(clock=clock),
        ),
        auto_apply=True,
    )
    result = await sweep.execute()
    assert result.networks_total == 1
    assert result.networks_drifting >= 1
    # Auto-apply прошёл хотя бы для одной сети.
    assert result.auto_applied >= 1


# ---------------------------------------------------------------------------
# RetentionSweep
# ---------------------------------------------------------------------------


async def test_retention_deletes_terminal_operations_only(clock: FrozenClock) -> None:
    operations = InMemoryOperationRepository()
    audit = InMemoryAuditEventRepository()

    def _make_op(op_id: str, status: OperationStatus, at: datetime) -> Operation:
        op = Operation.accept(
            operation_id=OperationId(op_id),
            kind=OperationKind.NETWORK_CREATE,
            resource=ResourceRef(type="network", id="net_x"),
            now=at,
        )
        if status is not OperationStatus.ACCEPTED:
            for step in (
                OperationStatus.PLANNING,
                OperationStatus.RUNNING,
                OperationStatus.VERIFYING,
                status,
            ):
                op.transition_to(step, now=at, message=step.value)
        return op

    # Старый succeeded → удаляем
    await operations.save(_make_op("op_old", OperationStatus.SUCCEEDED, _NOW - timedelta(days=100)))
    # Старый, но не терминальный → НЕ удаляем
    await operations.save(
        _make_op("op_pending", OperationStatus.ACCEPTED, _NOW - timedelta(days=100))
    )
    # Свежий succeeded → НЕ удаляем
    await operations.save(_make_op("op_new", OperationStatus.SUCCEEDED, _NOW))

    clock.current = _NOW
    sweep = RetentionSweep(
        operations=operations,
        audit_events=audit,
        audit_archive=NoopAuditArchive(),
        clock=clock,
        operation_retention_days=90,
        audit_retention_days=365,
    )
    result = await sweep.execute()
    assert result.operations_deleted == 1
    assert (await operations.get(OperationId("op_old"))) is None
    assert (await operations.get(OperationId("op_pending"))) is not None
    assert (await operations.get(OperationId("op_new"))) is not None


async def test_retention_archives_audit_to_file(
    clock: FrozenClock,
    tmp_path: Path,
) -> None:
    operations = InMemoryOperationRepository()
    audit = InMemoryAuditEventRepository()
    # Закинем два старых события и одно свежее.
    await audit.save(
        AuditEvent(
            id=AuditEventId("audit_old1"),
            at=_NOW - timedelta(days=400),
            action="network.create",
            resource_type="network",
        )
    )
    await audit.save(
        AuditEvent(
            id=AuditEventId("audit_old2"),
            at=_NOW - timedelta(days=380),
            action="network.update",
            resource_type="network",
        )
    )
    await audit.save(
        AuditEvent(
            id=AuditEventId("audit_new"),
            at=_NOW,
            action="network.create",
            resource_type="network",
        )
    )

    clock.current = _NOW
    sweep = RetentionSweep(
        operations=operations,
        audit_events=audit,
        audit_archive=FileAuditArchive(tmp_path),
        clock=clock,
        operation_retention_days=90,
        audit_retention_days=365,
    )
    result = await sweep.execute()

    assert result.audit_archived == 2
    assert result.audit_deleted == 2
    # Свежий остаётся
    remaining = await audit.list()
    assert [it.id for it in remaining] == ["audit_new"]
    # И в директории появились jsonl-файлы.
    files = list(tmp_path.glob("audit-*.jsonl"))  # noqa: ASYNC240 — pytest tmp_path
    assert files, "archive должен создать хотя бы один файл"
