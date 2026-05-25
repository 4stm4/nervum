"""Add project_id to networks/nodes; schema_version + project_id to outbox_events (N0)

Three-step pattern for each NOT NULL→nullable column addition:
  Step 1: add column as nullable (this migration)
  Step 2: backfill is not needed — column stays nullable (existing rows = global scope)
  Step 3: no tightening needed — project_id is intentionally optional

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # N0-02: project scope on primary resources (nullable — existing = global)
    op.add_column("networks", sa.Column("project_id", sa.String(length=64), nullable=True))
    op.add_column("nodes", sa.Column("project_id", sa.String(length=64), nullable=True))

    # N0-04: outbox envelope v2
    op.add_column(
        "outbox_events",
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "outbox_events",
        sa.Column("project_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("outbox_events", "project_id")
    op.drop_column("outbox_events", "schema_version")
    op.drop_column("nodes", "project_id")
    op.drop_column("networks", "project_id")
