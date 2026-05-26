"""N4: таблицы project_quotas, resource_snapshots, retention_policies,
gateway_bonds, load_balancers, lb_listeners, lb_pools, lb_members,
health_monitors.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # N4-01 — квоты проекта
    op.create_table(
        "project_quotas",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("project_id", sa.String, unique=True, nullable=False),
        sa.Column("limits", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_project_quotas_project_id", "project_quotas", ["project_id"])

    # N4-03 — версионированные снапшоты ресурсов
    op.create_table(
        "resource_snapshots",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("project_id", sa.String, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("label", sa.String, nullable=False, server_default=""),
        sa.Column("resource_types", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("payload", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_resource_snapshots_project_id", "resource_snapshots", ["project_id"])
    op.create_index("ix_resource_snapshots_version", "resource_snapshots", ["project_id", "version"])

    # N4-05 — политики хранения данных
    op.create_table(
        "retention_policies",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("scope", sa.String, nullable=False),
        sa.Column("retention_days", sa.Integer, nullable=False),
        sa.Column("project_id", sa.String, nullable=True),
        sa.Column("description", sa.String, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("scope", "project_id", name="uq_retention_scope_project"),
    )
    op.create_index("ix_retention_policies_scope", "retention_policies", ["scope"])
    op.create_index("ix_retention_policies_project_id", "retention_policies", ["project_id"])

    # N4-04 — bond-интерфейсы Gateway HA
    op.create_table(
        "gateway_bonds",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("node_id", sa.String, nullable=False),
        sa.Column("bond_name", sa.String, nullable=False),
        sa.Column("mode", sa.String, nullable=False, server_default="none"),
        sa.Column("members", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("mtu", sa.Integer, nullable=False, server_default="1500"),
        sa.Column("project_id", sa.String, nullable=True),
        sa.Column("applied_config", sa.Text, nullable=True),
        sa.Column("applied_at", sa.DateTime, nullable=True),
        sa.Column("labels", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_gateway_bonds_node_id", "gateway_bonds", ["node_id"])
    op.create_index("ix_gateway_bonds_project_id", "gateway_bonds", ["project_id"])

    # N4-06 — балансировщики нагрузки
    op.create_table(
        "load_balancers",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("vip_address", sa.String, nullable=False),
        sa.Column("vip_network_id", sa.String, nullable=False),
        sa.Column("project_id", sa.String, nullable=True),
        sa.Column("router_id", sa.String, nullable=True),
        sa.Column("description", sa.String, nullable=False, server_default=""),
        sa.Column("provider", sa.String, nullable=False, server_default="haproxy"),
        sa.Column("status", sa.String, nullable=False, server_default="build"),
        sa.Column("admin_state_up", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("applied_config", sa.Text, nullable=True),
        sa.Column("applied_at", sa.DateTime, nullable=True),
        sa.Column("labels", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_load_balancers_project_id", "load_balancers", ["project_id"])
    op.create_index("ix_load_balancers_status", "load_balancers", ["status"])

    # N4-06 — listener'ы балансировщика
    op.create_table(
        "lb_listeners",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column(
            "lb_id",
            sa.String,
            sa.ForeignKey("load_balancers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("protocol", sa.String, nullable=False),
        sa.Column("protocol_port", sa.Integer, nullable=False),
        sa.Column("default_pool_id", sa.String, nullable=True),
        sa.Column("description", sa.String, nullable=False, server_default=""),
        sa.Column("labels", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_lb_listeners_lb_id", "lb_listeners", ["lb_id"])

    # N4-06 — пулы балансировщика
    op.create_table(
        "lb_pools",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column(
            "lb_id",
            sa.String,
            sa.ForeignKey("load_balancers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("protocol", sa.String, nullable=False),
        sa.Column("lb_algorithm", sa.String, nullable=False, server_default="round_robin"),
        sa.Column("session_persistence", sa.String, nullable=False, server_default="none"),
        sa.Column("description", sa.String, nullable=False, server_default=""),
        sa.Column("labels", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_lb_pools_lb_id", "lb_pools", ["lb_id"])

    # N4-06 — участники пула
    op.create_table(
        "lb_members",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column(
            "pool_id",
            sa.String,
            sa.ForeignKey("lb_pools.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("address", sa.String, nullable=False),
        sa.Column("protocol_port", sa.Integer, nullable=False),
        sa.Column("weight", sa.Integer, nullable=False, server_default="1"),
        sa.Column("admin_state_up", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_lb_members_pool_id", "lb_members", ["pool_id"])

    # N4-07 — health monitor'ы пулов
    op.create_table(
        "health_monitors",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column(
            "pool_id",
            sa.String,
            sa.ForeignKey("lb_pools.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("check_type", sa.String, nullable=False),
        sa.Column("delay", sa.Integer, nullable=False, server_default="5"),
        sa.Column("timeout", sa.Integer, nullable=False, server_default="3"),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default="3"),
        sa.Column("url_path", sa.String, nullable=False, server_default="/health"),
        sa.Column("http_method", sa.String, nullable=False, server_default="GET"),
        sa.Column("expected_codes", sa.String, nullable=False, server_default="200"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("health_monitors")
    op.drop_table("lb_members")
    op.drop_table("lb_pools")
    op.drop_table("lb_listeners")
    op.drop_table("load_balancers")
    op.drop_table("gateway_bonds")
    op.drop_table("retention_policies")
    op.drop_table("resource_snapshots")
    op.drop_table("project_quotas")
