"""Platform schema: groups, schedules, attendance, credits, payments, settings, users, audit.

Additive only: new tables, new nullable/defaulted columns, new indexes.
``lessons.student_id`` becomes nullable (group lessons have no single student).
No column is dropped or renamed, no row is deleted.

Revision ID: 0002_platform_schema
Revises: 0001_baseline
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_platform_schema"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120)),
        sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", TZ),
        sa.Column("last_login_at", TZ),
        sa.Column("created_at", TZ, nullable=False),
    )

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("teacher_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("teacher_phone", sa.String(40), nullable=False, server_default=""),
        sa.Column("teacher_email", sa.String(120), nullable=False, server_default=""),
        sa.Column("subject", sa.String(120), nullable=False, server_default=""),
        sa.Column("address", sa.String(255), nullable=False, server_default=""),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("language", sa.String(2), nullable=False, server_default="ar"),
        sa.Column("default_country_code", sa.String(4), nullable=False, server_default="39"),
        sa.Column("default_price_cents", sa.Integer()),
        sa.Column("default_duration_min", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("charge_present", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("charge_late", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("charge_absent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("charge_excused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("charge_makeup", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("low_balance_threshold", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payment_due_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("receipt_footer", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", TZ),
    )

    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("subject", sa.String(120)),
        sa.Column("level", sa.String(60)),
        sa.Column("color", sa.String(7), nullable=False, server_default="#2f6f4f"),
        sa.Column("default_duration_min", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", TZ),
    )

    # ── students: new profile columns ────────────────────────────────────────
    with op.batch_alter_table("students") as b:
        b.add_column(sa.Column("phone", sa.String(40)))
        b.add_column(sa.Column("guardian_name", sa.String(120)))
        b.add_column(sa.Column("guardian_phone", sa.String(40)))
        b.add_column(sa.Column("school_grade", sa.String(60)))
        b.add_column(sa.Column("school", sa.String(120)))
        b.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="active"))
        b.add_column(sa.Column("enrollment_date", sa.Date()))
        b.add_column(sa.Column("billing_mode", sa.String(16), nullable=False, server_default="postpaid"))
        b.add_column(sa.Column("price_per_lesson_cents", sa.Integer()))
        b.add_column(sa.Column("message_language", sa.String(2)))
        b.add_column(sa.Column("notes", sa.Text()))
        b.add_column(sa.Column("created_at", TZ))
        b.add_column(sa.Column("updated_at", TZ))
        b.create_check_constraint("ck_students_status", "status IN ('active','paused','archived')")
        b.create_check_constraint("ck_students_billing_mode", "billing_mode IN ('postpaid','prepaid')")

    op.create_table(
        "group_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("joined_on", sa.Date(), nullable=False),
        sa.Column("left_on", sa.Date()),
    )
    op.create_index("ix_group_members_group_id", "group_members", ["group_id"])
    op.create_index("ix_group_members_student_id", "group_members", ["student_id"])
    op.create_index(
        "uq_group_members_active", "group_members", ["group_id", "student_id"], unique=True,
        postgresql_where=sa.text("left_on IS NULL"), sqlite_where=sa.text("left_on IS NULL"),
    )

    op.create_table(
        "schedule_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE")),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id")),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", TZ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_slots_weekday"),
        sa.CheckConstraint(
            "(group_id IS NOT NULL AND student_id IS NULL) OR (group_id IS NULL AND student_id IS NOT NULL)",
            name="ck_slots_owner",
        ),
    )
    op.create_index("ix_schedule_slots_group_id", "schedule_slots", ["group_id"])
    op.create_index("ix_schedule_slots_student_id", "schedule_slots", ["student_id"])

    # ── lessons: become sessions with a notebook ─────────────────────────────
    with op.batch_alter_table("lessons") as b:
        b.alter_column("student_id", existing_type=sa.Integer(), nullable=True)
        b.add_column(sa.Column("group_id", sa.Integer()))
        b.add_column(sa.Column("slot_id", sa.Integer()))
        b.add_column(sa.Column("slot_date", sa.Date()))
        b.add_column(sa.Column("duration_min", sa.Integer(), nullable=False, server_default="60"))
        b.add_column(sa.Column("status", sa.String(12), nullable=False, server_default="completed"))
        b.add_column(sa.Column("kind", sa.String(12), nullable=False, server_default="regular"))
        b.add_column(sa.Column("is_free", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("title", sa.String(200)))
        b.add_column(sa.Column("topics", sa.Text()))
        b.add_column(sa.Column("notes", sa.Text()))
        b.add_column(sa.Column("homework", sa.Text()))
        b.add_column(sa.Column("observations", sa.Text()))
        b.add_column(sa.Column("cancel_reason", sa.String(255)))
        b.add_column(sa.Column("cancelled_at", TZ))
        b.add_column(sa.Column("rescheduled_from", sa.String(16)))
        b.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        b.add_column(sa.Column("created_at", TZ))
        b.add_column(sa.Column("updated_at", TZ))
        b.create_foreign_key("fk_lessons_group_id", "groups", ["group_id"], ["id"])
        b.create_foreign_key("fk_lessons_slot_id", "schedule_slots", ["slot_id"], ["id"])
        b.create_check_constraint("ck_lessons_status", "status IN ('scheduled','completed','cancelled')")
        b.create_check_constraint("ck_lessons_kind", "kind IN ('regular','makeup','extra','trial')")
    op.create_index("ix_lessons_student_id", "lessons", ["student_id"])
    op.create_index("ix_lessons_group_id", "lessons", ["group_id"])
    op.create_index("ix_lessons_date", "lessons", ["date"])
    op.create_index("ix_lessons_date_time", "lessons", ["date", "time"])
    op.create_index("uq_lessons_slot_occurrence", "lessons", ["slot_id", "slot_date"], unique=True)

    op.create_table(
        "attendance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lesson_id", sa.Integer(), sa.ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("homework_status", sa.String(10)),
        sa.Column("performance", sa.SmallInteger()),
        sa.Column("note", sa.String(500)),
        sa.Column("created_at", TZ),
        sa.Column("updated_at", TZ),
        sa.UniqueConstraint("lesson_id", "student_id", name="uq_attendance_lesson_student"),
        sa.CheckConstraint(
            "status IN ('present','late','absent','excused','makeup','cancelled')", name="ck_attendance_status"
        ),
        sa.CheckConstraint("performance IS NULL OR performance BETWEEN 1 AND 5", name="ck_attendance_perf"),
    )
    op.create_index("ix_attendance_lesson_id", "attendance", ["lesson_id"])
    op.create_index("ix_attendance_student_id", "attendance", ["student_id"])

    op.create_table(
        "packages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("lessons", sa.Integer(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", TZ),
        sa.CheckConstraint("lessons > 0", name="ck_packages_lessons"),
        sa.CheckConstraint("price_cents >= 0", name="ck_packages_price"),
    )

    op.create_table(
        "charges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("description", sa.String(200), nullable=False, server_default=""),
        sa.Column("lessons", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_price_cents", sa.Integer()),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("packages.id")),
        sa.Column("charged_on", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), unique=True),
        sa.Column("voided_at", TZ),
        sa.Column("void_reason", sa.String(255)),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.CheckConstraint("lessons >= 0", name="ck_charges_lessons"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_charges_amount"),
    )
    op.create_index("ix_charges_student_id", "charges", ["student_id"])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(10), nullable=False, server_default="cash"),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("receipt_no", sa.String(20), nullable=False, unique=True),
        sa.Column("note", sa.String(255)),
        sa.Column("idempotency_key", sa.String(64), unique=True),
        sa.Column("voided_at", TZ),
        sa.Column("void_reason", sa.String(255)),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.CheckConstraint("amount_cents > 0", name="ck_payments_amount"),
        sa.CheckConstraint("method IN ('cash','bank','card','other')", name="ck_payments_method"),
    )
    op.create_index("ix_payments_student_id", "payments", ["student_id"])
    op.create_index("ix_payments_paid_on", "payments", ["paid_on"])

    op.create_table(
        "credit_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("charge_id", sa.Integer(), sa.ForeignKey("charges.id")),
        sa.Column("attendance_id", sa.Integer(), sa.ForeignKey("attendance.id", ondelete="SET NULL")),
        sa.Column("reverses_id", sa.Integer(), sa.ForeignKey("credit_entries.id"), unique=True),
        sa.Column("reversed_at", TZ),
        sa.Column("note", sa.String(255)),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.CheckConstraint("delta <> 0", name="ck_credit_delta"),
        sa.CheckConstraint("kind IN ('opening','purchase','consume','adjust','reversal')", name="ck_credit_kind"),
    )
    op.create_index("ix_credit_entries_student_id", "credit_entries", ["student_id"])
    op.create_index(
        "uq_credit_consume_active", "credit_entries", ["attendance_id"], unique=True,
        postgresql_where=sa.text("kind = 'consume' AND reversed_at IS NULL"),
        sqlite_where=sa.text("kind = 'consume' AND reversed_at IS NULL"),
    )
    op.create_index(
        "uq_credit_purchase_active", "credit_entries", ["charge_id"], unique=True,
        postgresql_where=sa.text("kind = 'purchase' AND reversed_at IS NULL"),
        sqlite_where=sa.text("kind = 'purchase' AND reversed_at IS NULL"),
    )

    op.create_table(
        "assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False, server_default="test"),
        sa.Column("taken_on", sa.Date(), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        sa.Column("max_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("note", sa.String(500)),
        sa.Column("created_at", TZ),
        sa.CheckConstraint("max_score > 0", name="ck_assessments_max"),
        sa.CheckConstraint("score >= 0 AND score <= max_score", name="ck_assessments_score"),
    )
    op.create_index("ix_assessments_student_id", "assessments", ["student_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", TZ, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("entity", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.Integer()),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id")),
        sa.Column("summary", sa.String(255), nullable=False, server_default=""),
        sa.Column("data", sa.Text()),
    )
    op.create_index("ix_audit_logs_at", "audit_logs", ["at"])
    op.create_index("ix_audit_logs_student_id", "audit_logs", ["student_id"])


def downgrade():
    # Intentionally not supported: this migration only adds structure and the
    # data step that follows writes into it. Restore from a Neon branch instead.
    raise RuntimeError("Downgrade not supported; restore from a Neon backup branch.")
