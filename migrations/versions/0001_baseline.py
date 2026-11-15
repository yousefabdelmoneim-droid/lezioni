"""Baseline: the schema of the original app (students, lessons).

Existing databases already have these tables: the boot step detects them and
*stamps* this revision instead of running it. Fresh databases run it.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "students",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("weekly_plan", sa.Integer(), nullable=False),
        sa.Column("lesson_days", sa.String(200), nullable=False, server_default=""),
        sa.Column("last_reset_date", sa.Date(), nullable=False),
    )
    op.create_table(
        "lessons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("time", sa.String(5), nullable=False),
        sa.Column("pagato", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_table("lessons")
    op.drop_table("students")
