"""N5: таблицы apply_schedules, mirror_sessions, vpn_tunnels, vpn_peers.

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # N5-01 — cron-расписания apply
    op.create_table(
        "apply_schedules",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("cron_expr", sa.String, nullable=False),
        sa.Column("target_type", sa.String, nullable=False),
        sa.Column("target_id", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("project_id", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("last_run_at", sa.DateTime, nullable=True),
        sa.Column("last_run_status", sa.String, nullable=True),
        sa.Column("labels", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    # N5-02 — port mirroring (SPAN/ERSPAN)
    op.create_table(
        "mirror_sessions",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("source_port_id", sa.String, nullable=False),
        sa.Column("direction", sa.String, nullable=False, server_default="both"),
        sa.Column("destination_port_id", sa.String, nullable=True),
        sa.Column("destination_ip", sa.String, nullable=True),
        sa.Column("filter_vlan", sa.Integer, nullable=True),
        sa.Column("project_id", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="inactive"),
        sa.Column("applied_config", sa.Text, nullable=True),
        sa.Column("applied_at", sa.DateTime, nullable=True),
        sa.Column("labels", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    # N5-05 — VPN туннели
    op.create_table(
        "vpn_tunnels",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("protocol", sa.String, nullable=False, server_default="wireguard"),
        sa.Column("local_endpoint", sa.String, nullable=False),
        sa.Column("remote_endpoint", sa.String, nullable=False),
        sa.Column("local_public_key", sa.String, nullable=False),
        sa.Column("remote_public_key", sa.String, nullable=False),
        sa.Column("listen_port", sa.Integer, nullable=False, server_default="51820"),
        sa.Column("preshared_key", sa.String, nullable=True),
        sa.Column("project_id", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="build"),
        sa.Column("applied_config", sa.Text, nullable=True),
        sa.Column("applied_at", sa.DateTime, nullable=True),
        sa.Column("labels", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    # N5-05 — WireGuard peer'ы (дочерняя таблица vpn_tunnels)
    op.create_table(
        "vpn_peers",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column(
            "tunnel_id",
            sa.String,
            sa.ForeignKey("vpn_tunnels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_key", sa.String, nullable=False),
        sa.Column("endpoint", sa.String, nullable=True),
        sa.Column("allowed_ips", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("persistent_keepalive", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("vpn_peers")
    op.drop_table("vpn_tunnels")
    op.drop_table("mirror_sessions")
    op.drop_table("apply_schedules")
