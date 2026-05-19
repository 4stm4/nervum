"""outbox_events table (M13: SDN-055)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sdn_controller.adapters.sql.models import UtcDateTime

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id", sa.String(length=64), unique=True, nullable=False),
        sa.Column("occurred_at", UtcDateTime(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("delivered_at", UtcDateTime(), nullable=True),
    )
    op.create_index("ix_outbox_events_id", "outbox_events", ["id"])
    op.create_index("ix_outbox_events_delivered_at", "outbox_events", ["delivered_at"])


def downgrade() -> None:
    op.drop_index("ix_outbox_events_delivered_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_id", table_name="outbox_events")
    op.drop_table("outbox_events")
