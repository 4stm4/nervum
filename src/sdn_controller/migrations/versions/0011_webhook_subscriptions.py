"""webhook_subscriptions table (M13: SDN-054)

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sdn_controller.adapters.sql.models import UtcDateTime

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("target_url", sa.String(length=2048), nullable=False),
        sa.Column("secret_hash", sa.String(length=128), nullable=False),
        sa.Column("event_types", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("last_delivery_at", UtcDateTime(), nullable=True),
        sa.Column("last_delivery_status", sa.String(length=255), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_webhook_subscriptions_state",
        "webhook_subscriptions",
        ["state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_subscriptions_state",
        table_name="webhook_subscriptions",
    )
    op.drop_table("webhook_subscriptions")
