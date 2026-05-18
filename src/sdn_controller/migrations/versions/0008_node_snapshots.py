"""node_snapshots table (M11: SDN-035)

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sdn_controller.adapters.sql.models import UtcDateTime

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_snapshots",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("agent_snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_node_snapshots_node_id_created",
        "node_snapshots",
        ["node_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_node_snapshots_node_id_created", table_name="node_snapshots")
    op.drop_table("node_snapshots")
