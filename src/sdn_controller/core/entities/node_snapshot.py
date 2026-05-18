"""Controller-side каталог снапшотов узлов (SDN-035).

Снапшот хранится **на агенте** — это нативная для agent'а штука
(``AgentPort.snapshot``/``restore`` уже есть из M3). На контроллере
мы держим лишь *ссылку*: id агента + наш собственный controller-id,
state_hash, label, время создания. По controller-id оператор делает
restore (контроллер вызовет ``agent.restore(agent_snapshot_id)``).

Контроллер-side каталог нужен, чтобы:
* CLI/UI могли показывать список снапшотов без обращения к
  агенту (его может не быть в сети);
* RBAC применялся к снапшоту как к ресурсу — оператор-узла видит
  только свои узлы, а админ — все.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import NodeId, NodeSnapshotId


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    id: NodeSnapshotId
    node_id: NodeId
    agent_snapshot_id: str
    state_hash: str
    created_at: datetime
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.agent_snapshot_id:
            raise ValidationError("agent_snapshot_id must be non-empty")
        if not self.state_hash:
            raise ValidationError("state_hash must be non-empty")


__all__ = ["NodeSnapshot"]
