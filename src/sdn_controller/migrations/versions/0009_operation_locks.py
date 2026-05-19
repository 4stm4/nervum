"""operation_locks table (M13: SDN-037)

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sdn_controller.adapters.sql.models import UtcDateTime

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operation_locks",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
    )
    op.create_index("ix_operation_locks_expires_at", "operation_locks", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_operation_locks_expires_at", table_name="operation_locks")
    op.drop_table("operation_locks")
