"""nodes.capabilities + enrollment_tokens (M2: SDN-005/006)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sdn_controller.adapters.sql.models import UtcDateTime

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite cannot ALTER columns in place — ``batch_alter_table`` recreates
    # the table; on PostgreSQL it executes as a plain ALTER.
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("capabilities", sa.JSON(), nullable=True))

    op.create_table(
        "enrollment_tokens",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("issued_at", UtcDateTime(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("used_at", UtcDateTime(), nullable=True),
        sa.Column("issued_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_enrollment_tokens_node_id",
        "enrollment_tokens",
        ["node_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_enrollment_tokens_node_id", table_name="enrollment_tokens")
    op.drop_table("enrollment_tokens")
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.drop_column("capabilities")
