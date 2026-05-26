"""N3: таблицы routers, floating_ips, bgp_peers.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routers",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.String, nullable=False, server_default=""),
        sa.Column("project_id", sa.String, nullable=True),
        sa.Column("external_network_id", sa.String, nullable=True),
        sa.Column("internal_network_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("static_routes", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("status", sa.String, nullable=False, server_default="build"),
        sa.Column("admin_state_up", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("ha_mode", sa.String, nullable=False, server_default="none"),
        sa.Column("vrrp_priority", sa.Integer, nullable=True),
        sa.Column("vrrp_vrid", sa.Integer, nullable=True),
        sa.Column("ipv6_config", sa.JSON, nullable=True),
        sa.Column("applied_config", sa.Text, nullable=True),
        sa.Column("applied_at", sa.DateTime, nullable=True),
        sa.Column("labels", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_routers_project_id", "routers", ["project_id"])

    op.create_table(
        "floating_ips",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("external_network_id", sa.String, nullable=False),
        sa.Column("floating_ip_address", sa.String, nullable=False),
        sa.Column("project_id", sa.String, nullable=True),
        sa.Column("fixed_ip_address", sa.String, nullable=True),
        sa.Column("logical_port_id", sa.String, nullable=True),
        sa.Column(
            "router_id",
            sa.String,
            sa.ForeignKey("routers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String, nullable=False, server_default="down"),
        sa.Column("labels", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_floating_ips_project_id", "floating_ips", ["project_id"])
    op.create_index("ix_floating_ips_router_id", "floating_ips", ["router_id"])

    op.create_table(
        "bgp_peers",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column(
            "router_id",
            sa.String,
            sa.ForeignKey("routers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("peer_ip", sa.String, nullable=False),
        sa.Column("peer_asn", sa.Integer, nullable=False),
        sa.Column("local_asn", sa.Integer, nullable=False),
        sa.Column("password", sa.String, nullable=False, server_default=""),
        sa.Column("state", sa.String, nullable=False, server_default="idle"),
        sa.Column("project_id", sa.String, nullable=True),
        sa.Column("labels", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_bgp_peers_router_id", "bgp_peers", ["router_id"])
    op.create_index("ix_bgp_peers_project_id", "bgp_peers", ["project_id"])


def downgrade() -> None:
    op.drop_table("bgp_peers")
    op.drop_table("floating_ips")
    op.drop_table("routers")
