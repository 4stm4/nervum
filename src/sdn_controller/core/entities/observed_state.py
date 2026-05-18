"""``ObservedState`` — the controller's per-node cache of agent-reported OVS state.

The diff engine compares ``ObservedState`` against the desired intent. Why a
controller-side copy of the agent's view? Two reasons:

* drift detection (M8) needs to compare without re-fetching every loop;
* the planner needs to read state for every node at once — if we always
  paged the network, we'd pay an RTT per node per plan.

Shape is a controller-domain projection: simpler than the agent's full
``OvsState`` (no internal admin_state, no ovs_version), keeps just what the
diff engine and the topology view care about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.ids import NodeId


@dataclass(frozen=True, slots=True)
class ObservedInterface:
    name: str
    type: str = "internal"
    options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ObservedPort:
    name: str
    interfaces: tuple[ObservedInterface, ...] = ()
    tag: int | None = None
    trunks: tuple[int, ...] = ()
    external_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ObservedBridge:
    name: str
    datapath_type: str = "system"
    ports: tuple[ObservedPort, ...] = ()
    external_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ObservedState:
    node_id: NodeId
    observed_at: datetime
    state_hash: str
    bridges: tuple[ObservedBridge, ...] = ()

    def find_bridge(self, name: str) -> ObservedBridge | None:
        for b in self.bridges:
            if b.name == name:
                return b
        return None
