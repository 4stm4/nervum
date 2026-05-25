"""Unit tests for Project entity and use cases (N0 — multitenancy)."""

from __future__ import annotations

import pytest

from sdn_controller.core.entities.project import Project
from sdn_controller.core.entities.project_member import ProjectMember
from sdn_controller.core.value_objects.errors import ConflictError, NotFoundError, ValidationError
from sdn_controller.core.value_objects.ids import ProjectId, ServiceAccountId
from sdn_controller.core.value_objects.security import Role
from sdn_controller.adapters.memory.repositories import (
    InMemoryProjectMemberRepository,
    InMemoryProjectRepository,
    InMemoryServiceAccountRepository,
)
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_clock():
    from sdn_controller.core.services.clock import SystemClock
    return SystemClock()


def _make_ids():
    from sdn_controller.core.value_objects.ids import UuidIdFactory
    return UuidIdFactory()


def _make_repos():
    return (
        InMemoryProjectRepository(),
        InMemoryProjectMemberRepository(),
        InMemoryServiceAccountRepository(),
    )


# ---------------------------------------------------------------------------
# Project entity validation
# ---------------------------------------------------------------------------


class TestProjectEntity:
    def _now(self):
        from datetime import UTC, datetime
        return datetime.now(UTC)

    def test_valid_project(self) -> None:
        now = self._now()
        p = Project(
            id=ProjectId("proj_abc"),
            name="Acme Core",
            slug="acme-core",
            created_at=now,
            updated_at=now,
        )
        assert p.slug == "acme-core"

    def test_slug_single_char(self) -> None:
        now = self._now()
        p = Project(
            id=ProjectId("proj_x"),
            name="X",
            slug="x",
            created_at=now,
            updated_at=now,
        )
        assert p.slug == "x"

    def test_invalid_slug_with_uppercase(self) -> None:
        now = self._now()
        with pytest.raises(ValidationError, match="slug"):
            Project(
                id=ProjectId("proj_a"),
                name="A",
                slug="UPPER",
                created_at=now,
                updated_at=now,
            )

    def test_invalid_slug_leading_dash(self) -> None:
        now = self._now()
        with pytest.raises(ValidationError, match="slug"):
            Project(
                id=ProjectId("proj_a"),
                name="A",
                slug="-bad",
                created_at=now,
                updated_at=now,
            )

    def test_empty_name_raises(self) -> None:
        now = self._now()
        with pytest.raises(ValidationError, match="name"):
            Project(
                id=ProjectId("proj_a"),
                name="",
                slug="a",
                created_at=now,
                updated_at=now,
            )

    def test_update_changes_name(self) -> None:
        from datetime import UTC, datetime, timedelta
        now = datetime.now(UTC)
        later = now + timedelta(seconds=5)
        p = Project(
            id=ProjectId("proj_a"),
            name="Old",
            slug="a",
            created_at=now,
            updated_at=now,
        )
        p.update(name="New", now=later)
        assert p.name == "New"
        assert p.updated_at == later


# ---------------------------------------------------------------------------
# Use case tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestCreateProject:
    async def test_creates_project(self) -> None:
        repos, _, _ = _make_repos()
        uc = CreateProject(projects=repos, clock=_make_clock(), ids=_make_ids())
        project = await uc.execute(
            CreateProjectCommand(name="Test", slug="test")
        )
        assert project.slug == "test"
        assert project.name == "Test"

    async def test_duplicate_slug_raises_conflict(self) -> None:
        repos, _, _ = _make_repos()
        uc = CreateProject(projects=repos, clock=_make_clock(), ids=_make_ids())
        await uc.execute(CreateProjectCommand(name="A", slug="same"))
        with pytest.raises(ConflictError):
            await uc.execute(CreateProjectCommand(name="B", slug="same"))


@pytest.mark.anyio
class TestGetProject:
    async def test_get_existing(self) -> None:
        repos, _, _ = _make_repos()
        create = CreateProject(projects=repos, clock=_make_clock(), ids=_make_ids())
        p = await create.execute(CreateProjectCommand(name="N", slug="n"))

        get = GetProject(projects=repos)
        found = await get.execute(p.id)
        assert found.id == p.id

    async def test_get_missing_raises(self) -> None:
        repos, _, _ = _make_repos()
        get = GetProject(projects=repos)
        with pytest.raises(NotFoundError):
            await get.execute(ProjectId("proj_nope"))


@pytest.mark.anyio
class TestListProjects:
    async def test_empty(self) -> None:
        repos, _, _ = _make_repos()
        projects = await ListProjects(projects=repos).execute()
        assert projects == []

    async def test_returns_all(self) -> None:
        repos, _, _ = _make_repos()
        create = CreateProject(projects=repos, clock=_make_clock(), ids=_make_ids())
        await create.execute(CreateProjectCommand(name="A", slug="a"))
        await create.execute(CreateProjectCommand(name="B", slug="b"))
        projects = await ListProjects(projects=repos).execute()
        assert len(projects) == 2


@pytest.mark.anyio
class TestUpdateProject:
    async def test_update_name(self) -> None:
        repos, _, _ = _make_repos()
        create = CreateProject(projects=repos, clock=_make_clock(), ids=_make_ids())
        p = await create.execute(CreateProjectCommand(name="Old", slug="old"))

        update = UpdateProject(projects=repos, clock=_make_clock())
        updated = await update.execute(
            UpdateProjectCommand(project_id=p.id, name="New")
        )
        assert updated.name == "New"

    async def test_update_missing_raises(self) -> None:
        repos, _, _ = _make_repos()
        with pytest.raises(NotFoundError):
            await UpdateProject(projects=repos, clock=_make_clock()).execute(
                UpdateProjectCommand(project_id=ProjectId("proj_x"), name="X")
            )


@pytest.mark.anyio
class TestDeleteProject:
    async def test_delete_existing(self) -> None:
        repos, _, _ = _make_repos()
        create = CreateProject(projects=repos, clock=_make_clock(), ids=_make_ids())
        p = await create.execute(CreateProjectCommand(name="D", slug="d"))

        await DeleteProject(projects=repos).execute(p.id)
        assert await repos.get(p.id) is None

    async def test_delete_missing_raises(self) -> None:
        repos, _, _ = _make_repos()
        with pytest.raises(NotFoundError):
            await DeleteProject(projects=repos).execute(ProjectId("proj_x"))


@pytest.mark.anyio
class TestProjectMembers:
    async def _setup(self):
        proj_repo, mem_repo, sa_repo = _make_repos()
        from sdn_controller.core.entities.service_account import ServiceAccount
        from sdn_controller.core.value_objects.security import Role
        from datetime import UTC, datetime

        create_proj = CreateProject(projects=proj_repo, clock=_make_clock(), ids=_make_ids())
        p = await create_proj.execute(CreateProjectCommand(name="P", slug="p"))

        now = datetime.now(UTC)
        sa = ServiceAccount(
            id=ServiceAccountId("sa_test"),
            name="test-sa",
            role=Role.VIEWER,
            created_at=now,
            updated_at=now,
        )
        await sa_repo.save(sa)
        return p, sa, proj_repo, mem_repo, sa_repo

    async def test_add_and_list_member(self) -> None:
        p, sa, proj_repo, mem_repo, sa_repo = await self._setup()
        add = AddProjectMember(
            projects=proj_repo,
            accounts=sa_repo,
            members=mem_repo,
            clock=_make_clock(),
        )
        m = await add.execute(
            AddMemberCommand(
                project_id=p.id,
                service_account_id=sa.id,
                role=Role.NETWORK_OPERATOR,
            )
        )
        assert m.role == Role.NETWORK_OPERATOR

        list_uc = ListProjectMembers(projects=proj_repo, members=mem_repo)
        members = await list_uc.execute(p.id)
        assert len(members) == 1

    async def test_remove_member(self) -> None:
        p, sa, proj_repo, mem_repo, sa_repo = await self._setup()
        add = AddProjectMember(
            projects=proj_repo,
            accounts=sa_repo,
            members=mem_repo,
            clock=_make_clock(),
        )
        await add.execute(
            AddMemberCommand(
                project_id=p.id,
                service_account_id=sa.id,
                role=Role.VIEWER,
            )
        )
        remove = RemoveProjectMember(projects=proj_repo, members=mem_repo)
        await remove.execute(p.id, sa.id)

        list_uc = ListProjectMembers(projects=proj_repo, members=mem_repo)
        members = await list_uc.execute(p.id)
        assert members == []

    async def test_add_member_to_missing_project_raises(self) -> None:
        _, _, proj_repo, mem_repo, sa_repo = await self._setup()
        add = AddProjectMember(
            projects=proj_repo,
            accounts=sa_repo,
            members=mem_repo,
            clock=_make_clock(),
        )
        with pytest.raises(NotFoundError):
            await add.execute(
                AddMemberCommand(
                    project_id=ProjectId("proj_nope"),
                    service_account_id=ServiceAccountId("sa_test"),
                    role=Role.VIEWER,
                )
            )

    async def test_add_member_with_missing_sa_raises(self) -> None:
        p, _, proj_repo, mem_repo, sa_repo = await self._setup()
        add = AddProjectMember(
            projects=proj_repo,
            accounts=sa_repo,
            members=mem_repo,
            clock=_make_clock(),
        )
        with pytest.raises(NotFoundError):
            await add.execute(
                AddMemberCommand(
                    project_id=p.id,
                    service_account_id=ServiceAccountId("sa_nope"),
                    role=Role.VIEWER,
                )
            )


@pytest.mark.anyio
class TestOutboxEnvelope:
    async def test_outbox_event_has_schema_version_2(self) -> None:
        from sdn_controller.core.entities.outbox import OutboxEvent
        from sdn_controller.core.value_objects.ids import OutboxEventId
        from datetime import UTC, datetime

        ev = OutboxEvent(
            id=OutboxEventId("outbox_test"),
            event_id=0,
            occurred_at=datetime.now(UTC),
            event_type="network.created",
            resource_type="network",
        )
        assert ev.schema_version == 2

    async def test_outbox_event_with_project_id(self) -> None:
        from sdn_controller.core.entities.outbox import OutboxEvent
        from sdn_controller.core.value_objects.ids import OutboxEventId
        from datetime import UTC, datetime

        ev = OutboxEvent(
            id=OutboxEventId("outbox_test2"),
            event_id=0,
            occurred_at=datetime.now(UTC),
            event_type="network.created",
            resource_type="network",
            project_id="proj_abc",
        )
        assert ev.project_id == "proj_abc"
