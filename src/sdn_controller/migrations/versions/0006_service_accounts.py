"""service_accounts + service_tokens + nodes.tls_thumbprint (M9: SDN-028/029/030)

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from sdn_controller.adapters.sql.models import UtcDateTime

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_accounts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("disabled_at", UtcDateTime(), nullable=True),
    )

    op.create_table(
        "service_tokens",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("service_account_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("issued_at", UtcDateTime(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=True),
        sa.Column("last_used_at", UtcDateTime(), nullable=True),
        sa.Column("revoked_at", UtcDateTime(), nullable=True),
        sa.Column("issued_by", sa.String(length=255), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["service_account_id"],
            ["service_accounts.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_service_tokens_account",
        "service_tokens",
        ["service_account_id"],
    )

    # Сбросим server_default у JSON labels, чтобы дальнейшие INSERT'ы
    # шли с прикладного слоя (как в остальных таблицах).
    with op.batch_alter_table("service_accounts", schema=None) as batch_op:
        batch_op.alter_column("labels", server_default=None)

    # M9 — pinned thumbprint серверного TLS-сертификата агента.
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tls_thumbprint", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("nodes", schema=None) as batch_op:
        batch_op.drop_column("tls_thumbprint")
    op.drop_index("ix_service_tokens_account", table_name="service_tokens")
    op.drop_table("service_tokens")
    op.drop_table("service_accounts")
