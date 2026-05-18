"""subnets.{dhcp,dns_zone} + networks.{nat,firewall_policy} (M7: SDN-023/024/025/026)

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Subnet: DHCP intent + authoritative DNS zone name. Both nullable so
    # existing rows survive the migration untouched.
    with op.batch_alter_table("subnets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("dhcp", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("dns_zone", sa.String(length=253), nullable=True))

    # Network: NAT spec + firewall policy. Both nullable for the same reason.
    with op.batch_alter_table("networks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("nat", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("firewall_policy", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("networks", schema=None) as batch_op:
        batch_op.drop_column("firewall_policy")
        batch_op.drop_column("nat")
    with op.batch_alter_table("subnets", schema=None) as batch_op:
        batch_op.drop_column("dns_zone")
        batch_op.drop_column("dhcp")
