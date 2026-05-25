"""projects + project_members tables (N0 — multitenancy)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sdn_controller.adapters.sql.models import UtcDateTime

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_projects_slug"),
    )
    op.create_index("ix_projects_slug", "projects", ["slug"])

    op.create_table(
        "project_members",
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("service_account_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("project_id", "service_account_id"),
    )
    op.create_index("ix_project_members_sa", "project_members", ["service_account_id"])


def downgrade() -> None:
    op.drop_index("ix_project_members_sa", table_name="project_members")
    op.drop_table("project_members")
    op.drop_index("ix_projects_slug", table_name="projects")
    op.drop_table("projects")
