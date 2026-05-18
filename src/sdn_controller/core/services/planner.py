"""Planner — fan a desired network out into per-node ``Plan``s.

Thin orchestrator over ``diff_engine.diff_for_node``: for each node that
participates in a network, run the diff and wrap the result in a ``Plan``
the agent can consume.

We keep ``Plan`` construction in this layer (rather than inside the diff
engine) so plan ids stay deterministic per planning run — useful for the
reconciler to correlate per-step results across all nodes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sdn_controller.core.entities import Network, Node, ObservedState
from sdn_controller.core.services.diff_engine import NodeAddress, diff_for_node
from sdn_controller.core.value_objects.ids import IdFactory, NodeId
from sdn_controller.ports.agent import Plan


@dataclass(frozen=True, slots=True)
class PerNodePlan:
    node_id: NodeId
    plan: Plan


class Planner:
    def __init__(self, *, ids: IdFactory) -> None:
        self._ids = ids

    def plan_for_network(
        self,
        *,
        network: Network,
        nodes: Mapping[NodeId, Node],
        observed_by_node: Mapping[NodeId, ObservedState],
    ) -> list[PerNodePlan]:
        """Build a plan per node that the network targets.

        Nodes that have no ``ObservedState`` yet are still planned for —
        the diff engine treats them as "empty observed", which yields a
        full-creation plan. This makes first-time apply work without
        a prior observation pass.
        """
        peers = [
            NodeAddress(node_id=nid, mgmt_ip=nodes[nid].mgmt_ip)
            for nid in network.node_ids
            if nid in nodes
        ]
        plans: list[PerNodePlan] = []
        for node_id in network.node_ids:
            if node_id not in nodes:
                continue  # ignore stale ids; AssignNetworkToNodes already validated
            observed = observed_by_node.get(node_id) or _empty_observed(node_id)
            steps = diff_for_node(
                network=network,
                local_node_id=node_id,
                peers=peers,
                observed=observed,
            )
            if not steps:
                continue
            plans.append(
                PerNodePlan(
                    node_id=node_id,
                    plan=Plan(plan_id=self._ids.operation(), steps=tuple(steps)),
                )
            )
        return plans


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _empty_observed(node_id: NodeId) -> ObservedState:
    # ``observed_at``/``state_hash`` don't matter to the diff engine; it only
    # reads ``bridges``. We use a fixed epoch so the planner stays I/O-free.
    return ObservedState(
        node_id=node_id,
        observed_at=_EPOCH,
        state_hash="",
        bridges=(),
    )


__all__ = ["PerNodePlan", "Planner"]
