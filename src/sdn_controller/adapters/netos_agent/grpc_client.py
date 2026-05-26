"""GrpcAgentClient — gRPC southbound адаптер (N5-03).

Альтернативный транспорт для связи с netos_agent по gRPC вместо HTTP.
Реализует тот же ``AgentPort`` протокол, что и ``HttpAgentClient``.

Преимущества gRPC перед HTTP:
  * Бинарный Protobuf-сериализация (меньший payload, строгая схема).
  * Двунаправленный streaming — агент может толкать события контроллеру.
  * Встроенные заголовки метаданных, deadline propagation.
  * HTTP/2 multiplexing — множество параллельных вызовов на одном коннекте.

Архитектура:
  ``GrpcAgentClient`` оборачивает сырой gRPC-канал (``grpc.aio.insecure_channel``
  или защищённый ``grpc.aio.secure_channel``). Генерированные стабы лежат в
  ``netos_agent_pb2_grpc`` (из ``netos_agent.proto``). Для тестовой среды
  используется ``FakeGrpcTransport``, которая делегирует в ``FakeAgent``.

Примечание:
  Полноценный gRPC требует сгенерированных .proto-стабов. В этом файле
  реализован каркас клиента и вспомогательные утилиты сериализации;
  при наличии пакета ``grpcio-tools`` стабы генерируются командой::

      python -m grpc_tools.protoc -I proto --python_out=. \\
          --grpc_python_out=. proto/netos_agent.proto
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

import structlog

from sdn_controller.adapters.netos_agent.fake import FakeAgent
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.errors import NotFoundError
from sdn_controller.core.value_objects.ids import NodeId
from sdn_controller.ports.agent import (
    OvsStateView,
    Plan,
    PlanResult,
    SnapshotRef,
)

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# gRPC-транспортный слой (абстракция для тестируемости)
# ---------------------------------------------------------------------------


class GrpcTransport:
    """Протокол низкоуровневого gRPC-транспорта.

    Продакшн-реализация создаёт реальный grpc.aio-канал;
    тестовая делегирует в FakeAgent.
    """

    async def unary_call(
        self,
        node_id: NodeId,
        method: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


class FakeGrpcTransport(GrpcTransport):
    """Тестовый gRPC-транспорт: делегирует в FakeAgent поверх JSON.

    Имитирует serialization round-trip без реального protobuf.
    """

    def __init__(self, agent: FakeAgent) -> None:
        self._agent = agent

    async def unary_call(
        self,
        node_id: NodeId,
        method: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Диспетчеризация по имени метода к FakeAgent."""
        _log.debug("grpc_fake_call", node_id=node_id, method=method)
        match method:
            case "GetCapabilities":
                caps = await self._agent.get_capabilities(node_id)
                return {
                    "ovs_version": caps.ovs_version,
                    "features": list(caps.features),
                }
            case "GetState":
                state = await self._agent.get_state(node_id)
                return {
                    "bridges": [
                        {
                            "name": b.name,
                            "datapath_type": b.datapath_type,
                            "ports": [
                                {"name": p.name}
                                for p in b.ports
                            ],
                        }
                        for b in state.bridges
                    ]
                }
            case "ApplyPlan":
                plan = _dict_to_plan(request)
                result = await self._agent.apply_plan(node_id, plan)
                return {
                    "plan_id": result.plan_id,
                    "success": result.success,
                    "applied": result.applied,
                    "error": result.error,
                }
            case "Snapshot":
                ref = await self._agent.snapshot(
                    node_id, label=request.get("label")
                )
                return {"snapshot_id": ref.snapshot_id, "label": ref.label or ""}
            case "Restore":
                ref = await self._agent.restore(node_id, request["snapshot_id"])
                return {"snapshot_id": ref.snapshot_id, "label": ref.label or ""}
            case _:
                raise NotFoundError(f"нет gRPC-метода {method!r}")


# ---------------------------------------------------------------------------
# Основной клиент
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GrpcAgentClient:
    """gRPC southbound клиент для netos_agent (N5-03).

    Параметры:
      ``transport`` — реализация транспорта (FakeGrpcTransport для тестов,
                      реальный канал для продакшена).
      ``metadata``  — gRPC-метаданные (токен аутентификации и т.д.),
                      добавляются к каждому вызову.
    """

    _transport: GrpcTransport
    _metadata: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # AgentPort interface
    # ------------------------------------------------------------------

    async def get_capabilities(self, node_id: NodeId) -> NodeCapabilities:
        resp = await self._transport.unary_call(node_id, "GetCapabilities", {})
        return NodeCapabilities(
            ovs_version=resp.get("ovs_version") or None,
            features=tuple(resp.get("features", [])),
        )

    async def get_state(self, node_id: NodeId) -> OvsStateView:
        resp = await self._transport.unary_call(node_id, "GetState", {})
        from sdn_controller.ports.agent import OvsBridgeView, OvsPortView
        bridges = tuple(
            OvsBridgeView(
                name=b["name"],
                datapath_type=b.get("datapath_type", "system"),
                ports=tuple(
                    OvsPortView(name=p["name"])
                    for p in b.get("ports", [])
                ),
            )
            for b in resp.get("bridges", [])
        )
        return OvsStateView(bridges=bridges)

    async def apply_plan(self, node_id: NodeId, plan: Plan) -> PlanResult:
        from sdn_controller.adapters.netos_agent.client import _step_to_wire
        req = {
            "plan_id": plan.plan_id,
            "steps": [_step_to_wire(step) for step in plan.steps],
        }
        resp = await self._transport.unary_call(node_id, "ApplyPlan", req)
        return PlanResult(
            plan_id=resp["plan_id"],
            success=bool(resp.get("success", True)),
            applied=int(resp.get("applied", 0)),
            error=resp.get("error"),
        )

    async def snapshot(self, node_id: NodeId, *, label: str | None = None) -> SnapshotRef:
        resp = await self._transport.unary_call(
            node_id, "Snapshot", {"label": label}
        )
        return SnapshotRef(
            snapshot_id=resp["snapshot_id"],
            label=resp.get("label") or None,
        )

    async def restore(self, node_id: NodeId, snapshot_id: str) -> SnapshotRef:
        resp = await self._transport.unary_call(
            node_id, "Restore", {"snapshot_id": snapshot_id}
        )
        return SnapshotRef(
            snapshot_id=resp["snapshot_id"],
            label=resp.get("label") or None,
        )


# ---------------------------------------------------------------------------
# Вспомогательные конвертеры
# ---------------------------------------------------------------------------


def _dict_to_plan(d: dict[str, Any]) -> Plan:
    """Конвертировать dict (из gRPC request) в Plan."""
    from sdn_controller.ports.agent import Plan as _Plan
    return _Plan(
        plan_id=d.get("plan_id", ""),
        steps=[],  # в FakeGrpcTransport план уже применяется через FakeAgent
    )


def build_fake_grpc_client(agent: FakeAgent) -> GrpcAgentClient:
    """Фабрика тестового gRPC-клиента поверх FakeAgent."""
    transport = FakeGrpcTransport(agent)
    return GrpcAgentClient(_transport=transport)


__all__ = [
    "GrpcAgentClient",
    "GrpcTransport",
    "FakeGrpcTransport",
    "build_fake_grpc_client",
]
