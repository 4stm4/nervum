"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sdn_controller.adapters.sql.models import UtcDateTime

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "networks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("mtu", sa.Integer(), nullable=False),
        sa.Column("vlan_id", sa.Integer(), nullable=True),
        sa.Column("vni", sa.Integer(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("intent_version", sa.Integer(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
    )

    op.create_table(
        "nodes",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("mgmt_ip", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("agent_version", sa.String(length=64), nullable=True),
        sa.Column("last_seen_at", UtcDateTime(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
    )

    op.create_table(
        "operations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("error_details", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_operations_created_at_id",
        "operations",
        ["created_at", "id"],
        unique=False,
    )

    op.create_table(
        "operation_events",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("at", UtcDateTime(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("operation_id", "sequence"),
    )

    op.create_table(
        "subnets",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("network_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("cidr", sa.String(length=64), nullable=False),
        sa.Column("gateway", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["network_id"], ["networks.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("subnets")
    op.drop_table("operation_events")
    op.drop_index("ix_operations_created_at_id", table_name="operations")
    op.drop_table("operations")
    op.drop_table("nodes")
    op.drop_table("networks")
