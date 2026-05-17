"""Agent-side aggregates: live OVS state and persisted snapshots."""

from netos_agent.core.entities.ovs_state import (
    BridgeState,
    InterfaceState,
    OvsState,
    PortState,
)
from netos_agent.core.entities.snapshot import OvsSnapshot

__all__ = [
    "BridgeState",
    "InterfaceState",
    "OvsSnapshot",
    "OvsState",
    "PortState",
]
