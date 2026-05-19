"""``ApplyNetwork`` — the M5 reconciler use case.

For a single network, walk the reconcile loop:

::

    accepted → planning → running → verifying → succeeded
                                              ↘ failed

Each stage is wrapped in a real action:

1. **planning** — fetch each target node's observed state via the agent,
   refresh the controller's cache, and ask the planner for per-node steps;
2. **running** — push the per-node plans to their agents in sequence
   (parallel comes later — keeps the failure model linear for M5);
3. **verifying** — re-observe each node and confirm
   ``is_in_compliance()`` holds; otherwise mark the operation failed.

If anything raises, the operation captures it in ``OperationError`` and
moves to ``failed``. The Operation events log each phase plus per-node
outcomes — that's the audit trail the controller exposes today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from sdn_controller.app.tracing import tracer
from sdn_controller.core.entities import (
    Network,
    ObservedBridge,
    ObservedInterface,
    ObservedPort,
    ObservedState,
    Operation,
    OperationError,
    ResourceRef,
)
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.services.diff_engine import NodeAddress, is_in_compliance
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.services.planner import PerNodePlan, Planner
from sdn_controller.core.value_objects.enums import OperationKind, OperationStatus
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import IdFactory, NetworkId, NodeId
from sdn_controller.ports.agent import (
    AgentPort,
    OvsBridgeView,
    OvsPortView,
    OvsStateView,
)
from sdn_controller.ports.locks import LockStore
from sdn_controller.ports.persistence import (
    NetworkRepository,
    NodeRepository,
    ObservedStateRepository,
    OperationRepository,
)

# Имя ключа лока — общее правило для всего апплая на одну сеть.
_LOCK_KEY_PREFIX = "network:apply:"
# 5 минут — типичный apply в M5/M7 укладывается, а потерянная задача
# сама высвободит лок до того, как оператор потеряет терпение.
_APPLY_LOCK_TTL_SECONDS = 300

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ApplyNetworkResult:
    network: Network
    operation: Operation


class ApplyNetwork:
    def __init__(
        self,
        *,
        networks: NetworkRepository,
        nodes: NodeRepository,
        observed_states: ObservedStateRepository,
        operations: OperationRepository,
        planner: Planner,
        agent: AgentPort,
        clock: Clock,
        ids: IdFactory,
        locks: LockStore,
        events: EventPublisher,
    ) -> None:
        self._networks = networks
        self._nodes = nodes
        self._observed = observed_states
        self._operations = operations
        self._planner = planner
        self._agent = agent
        self._clock = clock
        self._ids = ids
        self._locks = locks
        self._events = events

    async def execute(
        self, network_id: NetworkId, *, requested_by: str | None = None
    ) -> ApplyNetworkResult:
        with tracer().start_as_current_span(
            "sdn.network.apply",
            attributes={
                "sdn.network_id": str(network_id),
                "sdn.requested_by": requested_by or "",
            },
        ) as span:
            return await self._execute_in_span(network_id, requested_by, span)

    async def _execute_in_span(
        self,
        network_id: NetworkId,
        requested_by: str | None,
        span: Any,
    ) -> ApplyNetworkResult:
        network = await self._networks.get(network_id)
        if network is None:
            raise NotFoundError(f"network {network_id} not found")
        span.set_attribute("sdn.intent_version", network.intent_version)
        span.set_attribute("sdn.spec_hash", network.spec_hash)
        span.set_attribute("sdn.node_count", len(network.node_ids))

        operation_id = self._ids.operation()
        lock_key = f"{_LOCK_KEY_PREFIX}{network_id}"
        # Берём лок до accept'а операции: если он не взят, в БД не
        # появится зависший accepted-row.
        acquired = await self._locks.try_lock(
            lock_key,
            owner=operation_id,
            ttl_seconds=_APPLY_LOCK_TTL_SECONDS,
        )
        if not acquired:
            holder = await self._locks.current_owner(lock_key)
            raise _ApplyAlreadyRunning(network_id=network_id, holder_operation_id=holder)

        now = self._clock.now()
        operation = Operation.accept(
            operation_id=operation_id,
            kind=OperationKind.NETWORK_APPLY,
            resource=ResourceRef(type="network", id=network_id),
            now=now,
            created_by=requested_by,
            message=f"apply network {network.name!r} (intent_version={network.intent_version})",
        )

        try:
            try:
                plans = await self._plan(network=network, operation=operation)
                await self._run(plans=plans, operation=operation)
                await self._verify(network=network, plans=plans, operation=operation)
            except DomainError as exc:
                await self._fail(operation=operation, error=exc)
                await self._operations.save(operation)
                await self._publish_apply_result(
                    network=network, operation=operation, ok=False, plan_count=0
                )
                return ApplyNetworkResult(network=network, operation=operation)
            except Exception as exc:
                await self._fail(operation=operation, error=_wrap_unexpected(exc))
                await self._operations.save(operation)
                await self._publish_apply_result(
                    network=network, operation=operation, ok=False, plan_count=0
                )
                raise
            else:
                operation.transition_to(
                    OperationStatus.SUCCEEDED,
                    now=self._clock.now(),
                    message=f"network {network.name!r} converged on {len(plans)} node(s)",
                )

            await self._operations.save(operation)
            await self._publish_apply_result(
                network=network, operation=operation, ok=True, plan_count=len(plans)
            )
            return ApplyNetworkResult(network=network, operation=operation)
        finally:
            await self._locks.release(lock_key, owner=operation_id)

    async def _publish_apply_result(
        self,
        *,
        network: Network,
        operation: Operation,
        ok: bool,
        plan_count: int,
    ) -> None:
        await self._events.publish(
            event_type="network.applied" if ok else "network.apply_failed",
            resource_type="network",
            resource_id=network.id,
            payload={
                "name": network.name,
                "intent_version": network.intent_version,
                "spec_hash": network.spec_hash,
                "operation_id": operation.id,
                "node_count": plan_count,
                "ok": ok,
            },
        )

    # -- phases -----------------------------------------------------------

    async def _plan(self, *, network: Network, operation: Operation) -> list[PerNodePlan]:
        operation.transition_to(
            OperationStatus.PLANNING,
            now=self._clock.now(),
            message="observing target nodes and computing diff",
        )
        if not network.node_ids:
            return []

        nodes = {}
        observed_by_node: dict[NodeId, ObservedState] = {}
        for node_id in network.node_ids:
            node = await self._nodes.get(node_id)
            if node is None:
                raise NotFoundError(
                    f"network {network.id} references missing node {node_id}",
                )
            nodes[node_id] = node
            view = await self._agent.get_state(node_id)
            obs = _state_view_to_observed(node_id=node_id, view=view, observed_at=self._clock.now())
            observed_by_node[node_id] = obs
            await self._observed.save(obs)

        plans = self._planner.plan_for_network(
            network=network,
            nodes=nodes,
            observed_by_node=observed_by_node,
        )
        operation.log(
            now=self._clock.now(),
            message=(
                f"planned {sum(len(p.plan.steps) for p in plans)} step(s) "
                f"across {len(plans)} node(s)"
            ),
            payload={
                "node_plans": [
                    {
                        "node_id": p.node_id,
                        "plan_id": p.plan.plan_id,
                        "step_count": len(p.plan.steps),
                    }
                    for p in plans
                ]
            },
        )
        return plans

    async def _run(self, *, plans: list[PerNodePlan], operation: Operation) -> None:
        operation.transition_to(
            OperationStatus.RUNNING,
            now=self._clock.now(),
            message="applying per-node plans",
        )
        for per_node in plans:
            result = await self._agent.apply_plan(per_node.node_id, per_node.plan)
            operation.log(
                now=self._clock.now(),
                message=(
                    f"node {per_node.node_id}: "
                    f"{'ok' if result.ok else 'failed'} "
                    f"({sum(1 for s in result.steps if s.changed)} changed, "
                    f"{sum(1 for s in result.steps if not s.ok)} failed)"
                ),
                payload={
                    "node_id": per_node.node_id,
                    "plan_id": result.plan_id,
                    "ok": result.ok,
                    "steps": [
                        {
                            "action": s.action,
                            "ok": s.ok,
                            "changed": s.changed,
                            "message": s.message,
                            "details": dict(s.details),
                        }
                        for s in result.steps
                    ],
                },
            )
            if not result.ok:
                raise _apply_failed(per_node.node_id, result.steps)

    async def _verify(
        self,
        *,
        network: Network,
        plans: list[PerNodePlan],
        operation: Operation,
    ) -> None:
        operation.transition_to(
            OperationStatus.VERIFYING,
            now=self._clock.now(),
            message="re-observing nodes to confirm convergence",
        )
        # We need ``peers`` to evaluate compliance again; reuse mgmt_ips
        # captured during ``_plan``. We round-trip via the node repo to stay
        # honest about freshness.
        peers: list[NodeAddress] = []
        for node_id in network.node_ids:
            node = await self._nodes.get(node_id)
            if node is None:
                raise NotFoundError(
                    f"network {network.id} references missing node {node_id}",
                )
            peers.append(NodeAddress(node_id=node_id, mgmt_ip=node.mgmt_ip))

        for per_node in plans:
            view = await self._agent.get_state(per_node.node_id)
            observed = _state_view_to_observed(
                node_id=per_node.node_id, view=view, observed_at=self._clock.now()
            )
            await self._observed.save(observed)
            if not is_in_compliance(
                network=network,
                local_node_id=per_node.node_id,
                peers=peers,
                observed=observed,
            ):
                raise _verify_failed(per_node.node_id)

    async def _fail(self, *, operation: Operation, error: DomainError) -> None:
        details: dict[str, Any] = {}
        if len(error.args) > 1 and isinstance(error.args[1], dict):
            details = error.args[1]
        operation.transition_to(
            OperationStatus.FAILED,
            now=self._clock.now(),
            message=error.message,
            error=OperationError(
                code=error.code,
                message=error.message,
                details=details,
            ),
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _state_view_to_observed(
    *, node_id: NodeId, view: OvsStateView, observed_at: Any
) -> ObservedState:
    return ObservedState(
        node_id=node_id,
        observed_at=observed_at,
        state_hash=view.state_hash,
        bridges=tuple(_bridge_view_to_observed(b) for b in view.bridges),
    )


def _bridge_view_to_observed(b: OvsBridgeView) -> ObservedBridge:
    return ObservedBridge(
        name=b.name,
        datapath_type=b.datapath_type,
        external_ids=dict(b.external_ids),
        ports=tuple(_port_view_to_observed(p) for p in b.ports),
    )


def _port_view_to_observed(p: OvsPortView) -> ObservedPort:
    return ObservedPort(
        name=p.name,
        tag=p.tag,
        trunks=tuple(p.trunks),
        external_ids=dict(p.external_ids),
        interfaces=tuple(
            ObservedInterface(name=i.name, type=i.type, options=dict(i.options))
            for i in p.interfaces
        ),
    )


def _apply_failed(node_id: NodeId, steps: tuple[Any, ...]) -> DomainError:
    failed = [s for s in steps if not s.ok]
    message = f"agent {node_id} failed {len(failed)}/{len(steps)} step(s)"
    err = ValidationError(message)
    err.code = "apply_failed"
    err.args = (
        message,
        {
            "node_id": node_id,
            "failed_steps": [
                {
                    "action": s.action,
                    "message": s.message,
                    "details": dict(s.details),
                }
                for s in failed
            ],
        },
    )
    return err


def _verify_failed(node_id: NodeId) -> DomainError:
    message = f"verification failed for node {node_id}: observed state still drifts"
    err = ValidationError(message)
    err.code = "verify_failed"
    err.args = (message, {"node_id": node_id})
    return err


def _wrap_unexpected(exc: Exception) -> DomainError:
    err = ValidationError(str(exc) or "unexpected error during apply")
    err.code = "internal_error"
    return err


def _ApplyAlreadyRunning(
    *, network_id: NetworkId, holder_operation_id: str | None
) -> ConflictError:
    """Specialised ``ConflictError`` (409): apply этой сети уже идёт.

    Передаём ``holder_operation_id`` в ``details`` — оператор/testum
    могут перейти на ``/operations/{id}`` и watch'нуть завершение.
    """
    message = (
        f"apply for network {network_id} is already running"
        if holder_operation_id is None
        else f"apply for network {network_id} is already running (operation {holder_operation_id})"
    )
    err = ConflictError(message, code="apply_already_running")
    err.args = (message, {"holder_operation_id": holder_operation_id})
    return err


__all__ = ["ApplyNetwork", "ApplyNetworkResult"]
