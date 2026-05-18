"""subnets.{dns_servers,allocation_pools,reserved_ranges} + ip_allocations (M6: SDN-020/021)

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sdn_controller.adapters.sql.models import UtcDateTime

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("subnets", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("dns_servers", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("allocation_pools", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("reserved_ranges", sa.JSON(), nullable=False, server_default="[]")
        )
    with op.batch_alter_table("subnets", schema=None) as batch_op:
        batch_op.alter_column("dns_servers", server_default=None)
        batch_op.alter_column("allocation_pools", server_default=None)
        batch_op.alter_column("reserved_ranges", server_default=None)

    op.create_table(
        "ip_allocations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("subnet_id", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("owner_type", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("allocated_at", UtcDateTime(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["subnet_id"], ["subnets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("subnet_id", "ip_address", name="uq_ip_allocations_subnet_ip"),
    )
    op.create_index("ix_ip_allocations_subnet_id", "ip_allocations", ["subnet_id"])
    op.create_index(
        "ix_ip_allocations_owner",
        "ip_allocations",
        ["owner_type", "owner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ip_allocations_owner", table_name="ip_allocations")
    op.drop_index("ix_ip_allocations_subnet_id", table_name="ip_allocations")
    op.drop_table("ip_allocations")
    with op.batch_alter_table("subnets", schema=None) as batch_op:
        batch_op.drop_column("reserved_ranges")
        batch_op.drop_column("allocation_pools")
        batch_op.drop_column("dns_servers")
