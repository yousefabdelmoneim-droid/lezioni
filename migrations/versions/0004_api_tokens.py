"""Mobile API tokens (additive: one new table).

Revision ID: 0004_api_tokens
Revises: 0003_legacy_backfill
Create Date: 2026-11-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_api_tokens"
down_revision = "0003_legacy_backfill"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])


def downgrade():
    op.drop_index("ix_api_tokens_user_id", table_name="api_tokens")
    op.drop_table("api_tokens")
