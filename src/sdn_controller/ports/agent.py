"""Southbound agent port.

The core depends on this protocol, not on a particular transport. Milestone 3
(NetOS Agent) will ship the first concrete adapter; the in-memory ``FakeAgent``
in tests is enough to exercise the planner and reconciler in isolation.

``NodeCapabilities`` itself lives in ``core/value_objects`` — it's part of the
``Node`` aggregate, not transport-specific. We re-export it here so adapter
code reads from a single import path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.ids import NodeId

__all__ = ["AgentPort", "NodeCapabilities", "Plan", "PlanResult", "PlanStepResult"]


@dataclass(frozen=True, slots=True)
class PlanStepResult:
    action: str
    ok: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanResult:
    plan_id: str
    ok: bool
    steps: tuple[PlanStepResult, ...] = ()


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    steps: tuple[dict[str, Any], ...]


class AgentPort(Protocol):
    async def get_capabilities(self, node_id: NodeId) -> NodeCapabilities: ...
    async def apply_plan(self, node_id: NodeId, plan: Plan) -> PlanResult: ...
