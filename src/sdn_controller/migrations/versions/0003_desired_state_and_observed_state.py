"""networks.node_ids/spec_hash + observed_states (M5: SDN-015/016)

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sdn_controller.adapters.sql.models import UtcDateTime

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite can't ALTER columns in place — batch_alter_table rewrites the
    # table; on PostgreSQL it's a plain ALTER. Defaults are needed for the
    # NOT NULL columns to backfill existing rows; we strip them after.
    with op.batch_alter_table("networks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("node_ids", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(
            sa.Column("spec_hash", sa.String(length=64), nullable=False, server_default="")
        )
    with op.batch_alter_table("networks", schema=None) as batch_op:
        batch_op.alter_column("node_ids", server_default=None)
        batch_op.alter_column("spec_hash", server_default=None)

    op.create_table(
        "observed_states",
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("observed_at", UtcDateTime(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("node_id"),
    )


def downgrade() -> None:
    op.drop_table("observed_states")
    with op.batch_alter_table("networks", schema=None) as batch_op:
        batch_op.drop_column("spec_hash")
        batch_op.drop_column("node_ids")
