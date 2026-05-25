"""Project endpoints (N0 — multitenancy).

/api/v1/projects — CRUD
/api/v1/projects/{project_id}/members — membership management
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status

from sdn_controller.adapters.http_api.auth import CurrentPrincipal, require
from sdn_controller.adapters.http_api.dependencies import ContainerDep
from sdn_controller.core.entities import Project, ProjectMember
from sdn_controller.core.use_cases.projects import (
    AddMemberCommand,
    AddProjectMember,
    CreateProject,
    CreateProjectCommand,
    DeleteProject,
    GetProject,
    ListProjectMembers,
    ListProjects,
    RemoveProjectMember,
    UpdateProject,
    UpdateProjectCommand,
)
from sdn_controller.core.value_objects.ids import ProjectId, ServiceAccountId
from sdn_controller.core.value_objects.security import Permission, Role
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Pydantic schemas (inline — small enough to not warrant a shared schemas.py)
# ---------------------------------------------------------------------------


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    id: str
    name: str
    slug: str
    description: str | None
    labels: dict[str, str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, p: Project) -> "ProjectOut":
        return cls(
            id=p.id,
            name=p.name,
            slug=p.slug,
            description=p.description,
            labels=dict(p.labels),
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class ProjectListResponse(BaseModel):
    items: list[ProjectOut]


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slug: str = Field(..., min_length=1, max_length=63)
    description: str | None = Field(default=None, max_length=512)
    labels: dict[str, str] = Field(default_factory=dict)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    labels: dict[str, str] | None = None


class MemberOut(BaseModel):
    project_id: str
    service_account_id: str
    role: str
    created_at: datetime
    created_by: str | None

    @classmethod
    def from_domain(cls, m: ProjectMember) -> "MemberOut":
        return cls(
            project_id=m.project_id,
            service_account_id=m.service_account_id,
            role=m.role.value,
            created_at=m.created_at,
            created_by=m.created_by,
        )


class MemberListResponse(BaseModel):
    items: list[MemberOut]


class AddMemberRequest(BaseModel):
    service_account_id: str
    role: str = Field(..., description="admin | network_operator | automation | viewer")


# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


def _create_project(container: ContainerDep) -> CreateProject:
    return container.create_project


def _list_projects(container: ContainerDep) -> ListProjects:
    return container.list_projects


def _get_project(container: ContainerDep) -> GetProject:
    return container.get_project


def _update_project(container: ContainerDep) -> UpdateProject:
    return container.update_project


def _delete_project(container: ContainerDep) -> DeleteProject:
    return container.delete_project


def _add_member(container: ContainerDep) -> AddProjectMember:
    return container.add_project_member


def _remove_member(container: ContainerDep) -> RemoveProjectMember:
    return container.remove_project_member


def _list_members(container: ContainerDep) -> ListProjectMembers:
    return container.list_project_members


CreateProjectDep = Annotated[CreateProject, Depends(_create_project)]
ListProjectsDep = Annotated[ListProjects, Depends(_list_projects)]
GetProjectDep = Annotated[GetProject, Depends(_get_project)]
UpdateProjectDep = Annotated[UpdateProject, Depends(_update_project)]
DeleteProjectDep = Annotated[DeleteProject, Depends(_delete_project)]
AddMemberDep = Annotated[AddProjectMember, Depends(_add_member)]
RemoveMemberDep = Annotated[RemoveProjectMember, Depends(_remove_member)]
ListMembersDep = Annotated[ListProjectMembers, Depends(_list_members)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List projects",
    dependencies=[Depends(require(Permission.PROJECT_READ))],
)
async def list_projects(use_case: ListProjectsDep) -> ProjectListResponse:
    projects = await use_case.execute()
    return ProjectListResponse(items=[ProjectOut.from_domain(p) for p in projects])


@router.post(
    "",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    dependencies=[Depends(require(Permission.PROJECT_WRITE))],
)
async def create_project(
    payload: ProjectCreateRequest,
    use_case: CreateProjectDep,
    principal: CurrentPrincipal,
) -> ProjectOut:
    project = await use_case.execute(
        CreateProjectCommand(
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            labels=dict(payload.labels),
            created_by=principal.name,
        )
    )
    return ProjectOut.from_domain(project)


@router.get(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Get a project",
    dependencies=[Depends(require(Permission.PROJECT_READ))],
)
async def get_project(
    project_id: str,
    use_case: GetProjectDep,
) -> ProjectOut:
    project = await use_case.execute(ProjectId(project_id))
    return ProjectOut.from_domain(project)


@router.patch(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Update a project (name/description/labels)",
    dependencies=[Depends(require(Permission.PROJECT_WRITE))],
)
async def update_project(
    project_id: str,
    payload: ProjectUpdateRequest,
    use_case: UpdateProjectDep,
) -> ProjectOut:
    project = await use_case.execute(
        UpdateProjectCommand(
            project_id=ProjectId(project_id),
            name=payload.name,
            description=payload.description,
            labels=payload.labels,
        )
    )
    return ProjectOut.from_domain(project)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a project",
    dependencies=[Depends(require(Permission.PROJECT_ADMIN))],
)
async def delete_project(
    project_id: str,
    use_case: DeleteProjectDep,
) -> None:
    await use_case.execute(ProjectId(project_id))


# -- Members ---------------------------------------------------------------


@router.get(
    "/{project_id}/members",
    response_model=MemberListResponse,
    summary="List project members",
    dependencies=[Depends(require(Permission.PROJECT_READ))],
)
async def list_members(
    project_id: str,
    use_case: ListMembersDep,
) -> MemberListResponse:
    members = await use_case.execute(ProjectId(project_id))
    return MemberListResponse(items=[MemberOut.from_domain(m) for m in members])


@router.put(
    "/{project_id}/members/{sa_id}",
    response_model=MemberOut,
    status_code=status.HTTP_200_OK,
    summary="Add or update a project member",
    dependencies=[Depends(require(Permission.PROJECT_ADMIN))],
)
async def add_member(
    project_id: str,
    sa_id: str,
    payload: AddMemberRequest,
    use_case: AddMemberDep,
    principal: CurrentPrincipal,
) -> MemberOut:
    member = await use_case.execute(
        AddMemberCommand(
            project_id=ProjectId(project_id),
            service_account_id=ServiceAccountId(sa_id),
            role=Role(payload.role),
            created_by=principal.name,
        )
    )
    return MemberOut.from_domain(member)


@router.delete(
    "/{project_id}/members/{sa_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a project member",
    dependencies=[Depends(require(Permission.PROJECT_ADMIN))],
)
async def remove_member(
    project_id: str,
    sa_id: str,
    use_case: RemoveMemberDep,
) -> None:
    await use_case.execute(ProjectId(project_id), ServiceAccountId(sa_id))
