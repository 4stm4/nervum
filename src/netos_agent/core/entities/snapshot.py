"""``OvsSnapshot`` — point-in-time persisted dump of OVS state.

Snapshots back the ``/v1/ovs/snapshot`` + ``/v1/ovs/restore`` endpoints. The
controller takes one before applying a risky plan and rolls back to it if
verification fails. Stored shape is intentionally opaque (``dict[str, Any]``)
so the same blob round-trips whether the source is the in-memory fake or a
real ``ovsdb-client backup`` dump.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from netos_agent.core.value_objects.ids import SnapshotId


@dataclass(slots=True)
class OvsSnapshot:
    id: SnapshotId
    created_at: datetime
    state_hash: str
    payload: dict[str, Any] = field(default_factory=dict)
    label: str | None = None
