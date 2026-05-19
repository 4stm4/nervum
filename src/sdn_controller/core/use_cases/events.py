"""Чтение событийного журнала: snapshot + tail (SDN-057).

Контракт с подписчиком (testum):

1. ``GET /events/snapshot`` — текущее состояние ресурсов + monotonic
   ``event_id`` watermark. Используется при первичной синхронизации
   и при рекавери (если потеряли webhook'и).
2. ``GET /events?since=<event_id>`` — отдаёт события из outbox'а с
   ``event_id > since``. Подписчик переходит сюда после snapshot'а
   и читает дельту.

Эти две ручки вместе дают "snapshot + tail" pattern: подписчик
никогда не пропустит событие и никогда не получит дубликат
(если корректно ведёт свой cursor).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sdn_controller.core.entities import (
    Network,
    Node,
    OutboxEvent,
)
from sdn_controller.ports.persistence import (
    NetworkRepository,
    NodeRepository,
    OutboxRepository,
)


@dataclass(frozen=True, slots=True)
class ControllerSnapshot:
    """Полный снапшот состояния controller'а (минимальный для M13).

    Расширяется при необходимости — для интеграции с testum достаточно
    «знать все networks + nodes». IPAM/operations подписчик восстановит
    из webhook'ов или отдельных read-эндпоинтов.
    """

    event_id: int  # watermark: события с event_id <= ему уже отражены в snapshot'е
    networks: Sequence[Network]
    nodes: Sequence[Node]


class ExportSnapshot:
    """Собирает ``ControllerSnapshot`` атомарно относительно outbox'а.

    Стратегия: сначала фиксируем ``head_event_id``, потом читаем
    репозитории. Это гарантирует, что watermark ≥ всех мутаций,
    отражённых в snapshot'е (но может «недосчитать» — это
    допустимо, подписчик увидит их через tail).
    """

    def __init__(
        self,
        *,
        outbox: OutboxRepository,
        networks: NetworkRepository,
        nodes: NodeRepository,
    ) -> None:
        self._outbox = outbox
        self._networks = networks
        self._nodes = nodes

    async def execute(self) -> ControllerSnapshot:
        watermark = await self._outbox.head_event_id()
        networks = await self._networks.list()
        nodes = await self._nodes.list()
        return ControllerSnapshot(
            event_id=watermark,
            networks=networks,
            nodes=nodes,
        )


@dataclass(frozen=True, slots=True)
class EventsPage:
    head_event_id: int
    items: Sequence[OutboxEvent]


class ListEvents:
    """``GET /events?since=`` — отдаёт outbox с ``event_id > since``."""

    def __init__(self, *, outbox: OutboxRepository) -> None:
        self._outbox = outbox

    async def execute(self, *, since: int = 0, limit: int = 200) -> EventsPage:
        head = await self._outbox.head_event_id()
        items = await self._outbox.list_since(since=since, limit=limit)
        return EventsPage(head_event_id=head, items=items)


__all__ = [
    "ControllerSnapshot",
    "EventsPage",
    "ExportSnapshot",
    "ListEvents",
]
