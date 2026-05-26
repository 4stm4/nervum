"""N2 — SecurityPolicy, SecurityPolicyRule, TrunkPort.

N2-01  Сущность SecurityPolicy с упорядоченными правилами
N2-02  Компилятор политик → nftables
N2-03  Жизненный цикл apply/verify
N2-04  Счётчики пакетов/байт на уровне правила
N2-05  Trunk Port (802.1q VLAN trunking)

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- security_policies (N2-01, N2-02, N2-03) --------------------------------
    op.create_table(
        "security_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(512), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("compiled_ruleset", sa.Text(), nullable=True),
        sa.Column("compiled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_security_policies_project_id", "security_policies", ["project_id"])
    op.create_index("ix_security_policies_status", "security_policies", ["status"])

    # -- security_policy_rules (N2-01, N2-04) -----------------------------------
    op.create_table(
        "security_policy_rules",
        sa.Column(
            "policy_id",
            sa.String(64),
            sa.ForeignKey("security_policies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rule_id", sa.String(64), primary_key=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),   # ingress | egress | both
        sa.Column("action", sa.String(16), nullable=False),      # allow | deny
        sa.Column("source_type", sa.String(32), nullable=False, server_default="any"),
        sa.Column("source_value", sa.String(255), nullable=False, server_default=""),
        sa.Column("destination_type", sa.String(32), nullable=False, server_default="any"),
        sa.Column("destination_value", sa.String(255), nullable=False, server_default=""),
        sa.Column("service_object_id", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("comment", sa.String(512), nullable=False, server_default=""),
        sa.Column("packet_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("byte_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_security_policy_rules_policy_id",
        "security_policy_rules",
        ["policy_id"],
    )

    # -- trunk_ports (N2-05) ----------------------------------------------------
    op.create_table(
        "trunk_ports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "node_id",
            sa.String(64),
            sa.ForeignKey("nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("logical_port_id", sa.String(64), nullable=True),
        sa.Column("vlan_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("native_vlan", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trunk_ports_node_id", "trunk_ports", ["node_id"])
    op.create_index("ix_trunk_ports_project_id", "trunk_ports", ["project_id"])


def downgrade() -> None:
    op.drop_table("trunk_ports")
    op.drop_table("security_policy_rules")
    op.drop_table("security_policies")
