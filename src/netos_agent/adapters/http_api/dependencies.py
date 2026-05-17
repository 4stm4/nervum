"""FastAPI dependency providers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from netos_agent.app.container import Container
from netos_agent.core.use_cases.apply_plan import ApplyPlan
from netos_agent.core.use_cases.get_state import (
    GetNodeState,
    GetOvsState,
    GetSystemInfo,
    GetSystemStats,
)
from netos_agent.core.use_cases.snapshots import ListSnapshots, Restore, Snapshot


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def _apply_plan(c: ContainerDep) -> ApplyPlan:
    return c.apply_plan


def _get_ovs_state(c: ContainerDep) -> GetOvsState:
    return c.get_ovs_state


def _get_node_state(c: ContainerDep) -> GetNodeState:
    return c.get_node_state


def _get_system_info(c: ContainerDep) -> GetSystemInfo:
    return c.get_system_info


def _get_system_stats(c: ContainerDep) -> GetSystemStats:
    return c.get_system_stats


def _snapshot(c: ContainerDep) -> Snapshot:
    return c.snapshot


def _restore(c: ContainerDep) -> Restore:
    return c.restore


def _list_snapshots(c: ContainerDep) -> ListSnapshots:
    return c.list_snapshots


ApplyPlanDep = Annotated[ApplyPlan, Depends(_apply_plan)]
GetOvsStateDep = Annotated[GetOvsState, Depends(_get_ovs_state)]
GetNodeStateDep = Annotated[GetNodeState, Depends(_get_node_state)]
GetSystemInfoDep = Annotated[GetSystemInfo, Depends(_get_system_info)]
GetSystemStatsDep = Annotated[GetSystemStats, Depends(_get_system_stats)]
SnapshotDep = Annotated[Snapshot, Depends(_snapshot)]
RestoreDep = Annotated[Restore, Depends(_restore)]
ListSnapshotsDep = Annotated[ListSnapshots, Depends(_list_snapshots)]
