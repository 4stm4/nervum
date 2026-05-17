"""Derive a node's observable status from its heartbeat timestamp.

Why a function instead of a persisted column? A background reaper would be
the alternative — wake every N seconds and flip ``online`` → ``stale`` once
the threshold is crossed. That's more moving parts (and a clock skew bug
waiting to happen). Computing on read keeps a single source of truth
(``last_seen_at``) and stays correct under any reader's wall clock.
"""

from __future__ import annotations

from datetime import datetime

from sdn_controller.core.entities import Node
from sdn_controller.core.value_objects.enums import NodeStatus


def derived_status(
    node: Node,
    *,
    now: datetime,
    stale_after_seconds: int,
    offline_after_seconds: int,
) -> NodeStatus:
    """Effective status to expose to readers.

    ``PENDING`` and ``DRAINING`` are explicit lifecycle states an operator set;
    we never let heartbeat-age override them. For everything else we trust the
    last heartbeat age.
    """
    if node.status in {NodeStatus.PENDING, NodeStatus.DRAINING}:
        return node.status
    if node.last_seen_at is None:
        return node.status

    age = (now - node.last_seen_at).total_seconds()
    if age >= offline_after_seconds:
        return NodeStatus.OFFLINE
    if age >= stale_after_seconds:
        return NodeStatus.STALE
    return NodeStatus.ONLINE


def apply_derived_status(
    node: Node,
    *,
    now: datetime,
    stale_after_seconds: int,
    offline_after_seconds: int,
) -> Node:
    """Return ``node`` mutated to carry its derived status.

    Use cases call this just before returning to readers so the API never
    shows a stale ``online`` snapshot. The persisted row is unchanged.
    """
    node.status = derived_status(
        node,
        now=now,
        stale_after_seconds=stale_after_seconds,
        offline_after_seconds=offline_after_seconds,
    )
    return node
