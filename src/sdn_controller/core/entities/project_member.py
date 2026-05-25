"""ProjectMember — project-scoped role binding (N0).

A ``ProjectMember`` grants a ``ServiceAccount`` a specific ``Role`` *within
a project*. When a resource (Network, Node) has a ``project_id``, RBAC
checks first whether the principal is a project member with the required
role before falling back to the global role check.

Global admins always pass — they bypass project membership checks entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sdn_controller.core.value_objects.ids import ProjectId, ServiceAccountId
from sdn_controller.core.value_objects.security import Role


@dataclass(frozen=True, slots=True)
class ProjectMember:
    project_id: ProjectId
    service_account_id: ServiceAccountId
    role: Role
    created_at: datetime
    created_by: str | None = None


__all__ = ["ProjectMember"]
