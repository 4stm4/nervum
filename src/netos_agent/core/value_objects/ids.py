"""Typed identifiers for the agent."""

from __future__ import annotations

import uuid
from typing import NewType, Protocol

PlanId = NewType("PlanId", str)
SnapshotId = NewType("SnapshotId", str)

_PREFIXES: dict[str, str] = {
    "PlanId": "plan",
    "SnapshotId": "snap",
}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class IdFactory(Protocol):
    def plan(self) -> PlanId: ...
    def snapshot(self) -> SnapshotId: ...


class UuidIdFactory:
    def plan(self) -> PlanId:
        return PlanId(_new_id(_PREFIXES["PlanId"]))

    def snapshot(self) -> SnapshotId:
        return SnapshotId(_new_id(_PREFIXES["SnapshotId"]))
