"""Project use cases (N0 — multitenancy).

CRUD for Project aggregates + membership management (add/remove members).

Project-scoped RBAC enforcement lives in the HTTP layer (``auth.py``) via
``require_project_access``. Use cases here trust their callers to have
already verified access.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from sdn_controller.core.entities import Project, ProjectMember
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.value_objects.errors import ConflictError, NotFoundError
from sdn_controller.core.value_objects.ids import IdFactory, ProjectId, ServiceAccountId
from sdn_controller.core.value_objects.security import Role
from sdn_controller.ports.persistence import (
    ProjectMemberRepository,
    ProjectRepository,
    ServiceAccountRepository,
)

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Commands & results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateProjectCommand:
    name: str
    slug: str
    description: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateProjectCommand:
    project_id: ProjectId
    name: str | None = None
    description: str | None = None
    labels: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class AddMemberCommand:
    project_id: ProjectId
    service_account_id: ServiceAccountId
    role: Role
    created_by: str | None = None


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


class CreateProject:
    def __init__(
        self,
        *,
        projects: ProjectRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._projects = projects
        self._clock = clock
        self._ids = ids

    async def execute(self, cmd: CreateProjectCommand) -> Project:
        existing = await self._projects.get_by_slug(cmd.slug)
        if existing is not None:
            raise ConflictError(
                f"project with slug {cmd.slug!r} already exists",
                code="project_slug_conflict",
            )
        now = self._clock.now()
        project = Project(
            id=self._ids.project(),
            name=cmd.name,
            slug=cmd.slug,
            description=cmd.description,
            labels=dict(cmd.labels),
            created_at=now,
            updated_at=now,
        )
        await self._projects.save(project)
        _log.info("project_created", project_id=project.id, slug=project.slug)
        return project


class GetProject:
    def __init__(self, *, projects: ProjectRepository) -> None:
        self._projects = projects

    async def execute(self, project_id: ProjectId) -> Project:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError(f"project {project_id} not found")
        return project


class GetProjectBySlug:
    def __init__(self, *, projects: ProjectRepository) -> None:
        self._projects = projects

    async def execute(self, slug: str) -> Project:
        project = await self._projects.get_by_slug(slug)
        if project is None:
            raise NotFoundError(f"project with slug {slug!r} not found")
        return project


class ListProjects:
    def __init__(self, *, projects: ProjectRepository) -> None:
        self._projects = projects

    async def execute(self) -> list[Project]:
        return await self._projects.list()


class UpdateProject:
    def __init__(self, *, projects: ProjectRepository, clock: Clock) -> None:
        self._projects = projects
        self._clock = clock

    async def execute(self, cmd: UpdateProjectCommand) -> Project:
        project = await self._projects.get(cmd.project_id)
        if project is None:
            raise NotFoundError(f"project {cmd.project_id} not found")
        project.update(
            name=cmd.name,
            description=cmd.description,
            labels=cmd.labels,
            now=self._clock.now(),
        )
        await self._projects.save(project)
        return project


class DeleteProject:
    def __init__(self, *, projects: ProjectRepository) -> None:
        self._projects = projects

    async def execute(self, project_id: ProjectId) -> None:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError(f"project {project_id} not found")
        await self._projects.delete(project_id)
        _log.info("project_deleted", project_id=project_id)


class AddProjectMember:
    def __init__(
        self,
        *,
        projects: ProjectRepository,
        accounts: ServiceAccountRepository,
        members: ProjectMemberRepository,
        clock: Clock,
    ) -> None:
        self._projects = projects
        self._accounts = accounts
        self._members = members
        self._clock = clock

    async def execute(self, cmd: AddMemberCommand) -> ProjectMember:
        project = await self._projects.get(cmd.project_id)
        if project is None:
            raise NotFoundError(f"project {cmd.project_id} not found")
        account = await self._accounts.get(cmd.service_account_id)
        if account is None:
            raise NotFoundError(f"service account {cmd.service_account_id} not found")
        member = ProjectMember(
            project_id=cmd.project_id,
            service_account_id=cmd.service_account_id,
            role=cmd.role,
            created_at=self._clock.now(),
            created_by=cmd.created_by,
        )
        await self._members.save(member)
        return member


class RemoveProjectMember:
    def __init__(
        self,
        *,
        projects: ProjectRepository,
        members: ProjectMemberRepository,
    ) -> None:
        self._projects = projects
        self._members = members

    async def execute(self, project_id: ProjectId, sa_id: ServiceAccountId) -> None:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError(f"project {project_id} not found")
        member = await self._members.get(project_id, sa_id)
        if member is None:
            raise NotFoundError(
                f"service account {sa_id} is not a member of project {project_id}"
            )
        await self._members.delete(project_id, sa_id)


class ListProjectMembers:
    def __init__(
        self,
        *,
        projects: ProjectRepository,
        members: ProjectMemberRepository,
    ) -> None:
        self._projects = projects
        self._members = members

    async def execute(self, project_id: ProjectId) -> list[ProjectMember]:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError(f"project {project_id} not found")
        return await self._members.list_for_project(project_id)


__all__ = [
    "AddMemberCommand",
    "AddProjectMember",
    "CreateProject",
    "CreateProjectCommand",
    "DeleteProject",
    "GetProject",
    "GetProjectBySlug",
    "ListProjectMembers",
    "ListProjects",
    "RemoveProjectMember",
    "UpdateProject",
    "UpdateProjectCommand",
]
