"""Сущность ResourceSnapshot v2 — мультиресурсный снапшот (N4-03).

В отличие от NodeSnapshot (снапшот конфига узла), ResourceSnapshot
сохраняет состояние доменных объектов проекта: сети, маршрутизаторы,
floating IP, BGP-пиры, балансировщики.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sdn_controller.core.value_objects.ids import ProjectId, ResourceSnapshotId


@dataclass
class ResourceSnapshot:
    """Версионированный снапшот ресурсов проекта (N4-03).

    ``version`` — монотонно возрастающий целочисленный номер в рамках проекта.
    ``resource_types`` — список типов ресурсов, включённых в снапшот.
    ``payload`` — JSON-сериализованные списки ресурсов.
    ``label`` — произвольная метка оператора (описание, тег релиза и т.п.).
    """

    id: ResourceSnapshotId
    project_id: ProjectId
    version: int
    label: str = ""
    resource_types: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def resource_count(self) -> int:
        """Суммарное число сохранённых ресурсов."""
        return sum(len(v) for v in self.payload.values() if isinstance(v, list))
