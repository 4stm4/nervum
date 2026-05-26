"""N1 — LogicalPort, SecurityGroup, AddressPool, ServiceObject, QosPolicy, maintenance.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- nodes: maintenance mode (N1-06) ------------------------------------
    op.add_column(
        "nodes",
        sa.Column("maintenance", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "nodes",
        sa.Column("maintenance_at", sa.DateTime(timezone=True), nullable=True),
    )

    # -- logical_ports (N1-01) ----------------------------------------------
    op.create_table(
        "logical_ports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "node_id",
            sa.String(64),
            sa.ForeignKey("nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "network_id",
            sa.String(64),
            sa.ForeignKey("networks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vif_id", sa.String(255), nullable=True),
        sa.Column("mac_address", sa.String(17), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_logical_ports_node_id", "logical_ports", ["node_id"])
    op.create_index("ix_logical_ports_network_id", "logical_ports", ["network_id"])
    op.create_index("ix_logical_ports_project_id", "logical_ports", ["project_id"])

    # -- security_groups (N1-02) -------------------------------------------
    op.create_table(
        "security_groups",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(512), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_security_groups_project_id", "security_groups", ["project_id"])

    op.create_table(
        "security_group_members",
        sa.Column(
            "sg_id",
            sa.String(64),
            sa.ForeignKey("security_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("member_type", sa.String(32), primary_key=True),
        sa.Column("member_value", sa.String(255), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sg_members_sg_id", "security_group_members", ["sg_id"])

    # -- address_pools (N1-03) --------------------------------------------
    op.create_table(
        "address_pools",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(512), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("cidrs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_address_pools_project_id", "address_pools", ["project_id"])

    # -- service_objects (N1-04) ------------------------------------------
    op.create_table(
        "service_objects",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(512), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("protocol", sa.String(16), nullable=False),
        sa.Column("ports", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_service_objects_project_id", "service_objects", ["project_id"])

    # -- qos_policies (N1-05) ---------------------------------------------
    op.create_table(
        "qos_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(512), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("ingress_kbps", sa.Integer(), nullable=True),
        sa.Column("egress_kbps", sa.Integer(), nullable=True),
        sa.Column("burst_kb", sa.Integer(), nullable=True),
        sa.Column("dscp", sa.Integer(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_qos_policies_project_id", "qos_policies", ["project_id"])


def downgrade() -> None:
    op.drop_table("qos_policies")
    op.drop_table("service_objects")
    op.drop_table("address_pools")
    op.drop_table("security_group_members")
    op.drop_table("security_groups")
    op.drop_table("logical_ports")
    op.drop_column("nodes", "maintenance_at")
    op.drop_column("nodes", "maintenance")
