"""Project aggregate (N0 — multitenancy).

A ``Project`` is the top-level isolation boundary. All primary resources
(Network, Node) optionally carry a ``project_id`` — when set, RBAC is
evaluated at the project level rather than globally.

Design notes:
* ``slug`` — URL-safe, lowercase, alphanumeric+dash identifier used in paths
  (e.g. ``/projects/acme-core``). Must be unique; immutable after creation.
* Global admins see/manage all projects. Project-scoped roles come via
  ``ProjectMember`` (see project_member.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import ProjectId

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,61}[a-z0-9]$|^[a-z0-9]$")
_MAX_NAME_LEN = 128
_MAX_DESC_LEN = 512


@dataclass(slots=True)
class Project:
    id: ProjectId
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime
    description: str | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("project name must be non-empty")
        if len(self.name) > _MAX_NAME_LEN:
            raise ValidationError(f"project name too long (max {_MAX_NAME_LEN})")
        if not _SLUG_RE.match(self.slug):
            raise ValidationError(
                f"project slug must be lowercase alphanumeric/dash "
                f"(1-63 chars, no leading/trailing dash): {self.slug!r}"
            )
        if self.description is not None and len(self.description) > _MAX_DESC_LEN:
            raise ValidationError(f"project description too long (max {_MAX_DESC_LEN})")

    def update(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        labels: dict[str, str] | None = None,
        now: datetime,
    ) -> None:
        """Mutate mutable fields. Slug is immutable."""
        if name is not None:
            if not name.strip():
                raise ValidationError("project name must be non-empty")
            if len(name) > _MAX_NAME_LEN:
                raise ValidationError(f"project name too long (max {_MAX_NAME_LEN})")
            self.name = name
        if description is not None:
            if len(description) > _MAX_DESC_LEN:
                raise ValidationError(f"project description too long (max {_MAX_DESC_LEN})")
            self.description = description
        if labels is not None:
            self.labels = dict(labels)
        self.updated_at = now


__all__ = ["Project"]
