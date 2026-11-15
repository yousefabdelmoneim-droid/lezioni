"""Database models.

The two original tables (``students`` and ``lessons``) are extended in place so
that every existing ID stays valid. Legacy columns (``weekly_plan``,
``lesson_days``, ``last_reset_date``, ``pagato``) are kept untouched.

Money is always stored as integer cents.
"""
from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .utils import today_rome


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(dt):
    """Aware UTC datetime from a DB value (SQLite returns naive UTC, PostgreSQL aware)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# ── Constants ────────────────────────────────────────────────────────────────

STUDENT_STATUSES = ("active", "paused", "archived")
BILLING_MODES = ("postpaid", "prepaid")
LESSON_STATUSES = ("scheduled", "completed", "cancelled")
LESSON_KINDS = ("regular", "makeup", "extra", "trial")
ATTENDANCE_STATUSES = ("present", "late", "absent", "excused", "makeup", "cancelled")
HOMEWORK_STATUSES = ("done", "partial", "missing")
PAYMENT_METHODS = ("cash", "bank", "card", "other")
CREDIT_KINDS = ("opening", "purchase", "consume", "adjust", "reversal")
ASSESSMENT_KINDS = ("exam", "test", "homework", "oral")


# ── Users & settings ─────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120))
    failed_logins = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    locked_until = db.Column(db.DateTime(timezone=True))
    last_login_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Settings(db.Model):
    """Single-row table (id = 1) with the teacher profile and policies."""

    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    teacher_name = db.Column(db.String(120), nullable=False, default="", server_default="")
    teacher_phone = db.Column(db.String(40), nullable=False, default="", server_default="")
    teacher_email = db.Column(db.String(120), nullable=False, default="", server_default="")
    subject = db.Column(db.String(120), nullable=False, default="", server_default="")
    address = db.Column(db.String(255), nullable=False, default="", server_default="")
    currency = db.Column(db.String(3), nullable=False, default="EUR", server_default="EUR")
    language = db.Column(db.String(2), nullable=False, default="ar", server_default="ar")
    default_country_code = db.Column(db.String(4), nullable=False, default="39", server_default="39")
    default_price_cents = db.Column(db.Integer)
    default_duration_min = db.Column(db.Integer, nullable=False, default=60, server_default="60")

    charge_present = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"))
    charge_late = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"))
    charge_absent = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"))
    charge_excused = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"))
    charge_makeup = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"))

    low_balance_threshold = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    payment_due_days = db.Column(db.Integer, nullable=False, default=30, server_default="30")
    receipt_footer = db.Column(db.Text, nullable=False, default="", server_default="")
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def charges_for(self, status):
        return {
            "present": self.charge_present,
            "late": self.charge_late,
            "absent": self.charge_absent,
            "excused": self.charge_excused,
            "makeup": self.charge_makeup,
            "cancelled": False,
        }.get(status, False)

    @classmethod
    def get(cls):
        s = db.session.get(cls, 1)
        if s is None:
            s = cls(id=1)
            db.session.add(s)
            db.session.flush()
        return s


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(60), nullable=False)
    entity = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.Integer)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), index=True)
    summary = db.Column(db.String(255), nullable=False, default="")
    data = db.Column(db.Text)

    user = db.relationship("User")


# ── Students & groups ────────────────────────────────────────────────────────

class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    # legacy columns (kept, no longer used by the UI)
    weekly_plan = db.Column(db.Integer, nullable=False, default=1)
    lesson_days = db.Column(db.String(200), nullable=False, server_default="", default="")
    last_reset_date = db.Column(db.Date, nullable=False, default=today_rome)

    phone = db.Column(db.String(40))
    guardian_name = db.Column(db.String(120))
    guardian_phone = db.Column(db.String(40))
    school_grade = db.Column(db.String(60))
    school = db.Column(db.String(120))
    status = db.Column(db.String(16), nullable=False, default="active", server_default="active")
    enrollment_date = db.Column(db.Date, default=today_rome)
    billing_mode = db.Column(db.String(16), nullable=False, default="postpaid", server_default="postpaid")
    price_per_lesson_cents = db.Column(db.Integer)
    message_language = db.Column(db.String(2))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    memberships = db.relationship("GroupMember", back_populates="student", lazy="select")

    __table_args__ = (
        CheckConstraint("status IN ('active','paused','archived')", name="ck_students_status"),
        CheckConstraint("billing_mode IN ('postpaid','prepaid')", name="ck_students_billing_mode"),
    )

    def active_groups(self, on=None):
        on = on or today_rome()
        return [m.group for m in self.memberships if m.is_active(on)]

    @property
    def initials(self):
        parts = [p for p in (self.name or "").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def __repr__(self):
        return f"<Student {self.id} {self.name!r}>"


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    subject = db.Column(db.String(120))
    level = db.Column(db.String(60))
    color = db.Column(db.String(7), nullable=False, default="#2f6f4f", server_default="#2f6f4f")
    default_duration_min = db.Column(db.Integer, nullable=False, default=60, server_default="60")
    active = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    memberships = db.relationship(
        "GroupMember", back_populates="group", lazy="select", order_by="GroupMember.joined_on"
    )

    def members(self, on=None):
        on = on or today_rome()
        return sorted(
            (m.student for m in self.memberships if m.is_active(on)),
            key=lambda s: s.name.lower(),
        )


class GroupMember(db.Model):
    __tablename__ = "group_members"

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)
    joined_on = db.Column(db.Date, nullable=False, default=today_rome)
    left_on = db.Column(db.Date)

    group = db.relationship("Group", back_populates="memberships")
    student = db.relationship("Student", back_populates="memberships")

    __table_args__ = (
        Index(
            "uq_group_members_active",
            "group_id",
            "student_id",
            unique=True,
            postgresql_where=text("left_on IS NULL"),
            sqlite_where=text("left_on IS NULL"),
        ),
    )

    def is_active(self, on):
        return self.joined_on <= on and (self.left_on is None or self.left_on > on)


class ScheduleSlot(db.Model):
    """A weekly recurring slot for a group or for a single student."""

    __tablename__ = "schedule_slots"

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), index=True)
    weekday = db.Column(db.Integer, nullable=False)  # 0 = Monday
    start_time = db.Column(db.String(5), nullable=False)  # HH:MM
    duration_min = db.Column(db.Integer, nullable=False, default=60, server_default="60")
    valid_from = db.Column(db.Date, nullable=False, default=today_rome)
    valid_until = db.Column(db.Date)
    active = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    group = db.relationship("Group")
    student = db.relationship("Student")

    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_slots_weekday"),
        CheckConstraint(
            "(group_id IS NOT NULL AND student_id IS NULL) OR (group_id IS NULL AND student_id IS NOT NULL)",
            name="ck_slots_owner",
        ),
    )

    @property
    def title(self):
        return self.group.name if self.group_id else self.student.name

    @property
    def color(self):
        return self.group.color if self.group_id else None


# ── Lessons & attendance ─────────────────────────────────────────────────────

class Lesson(db.Model):
    __tablename__ = "lessons"

    id = db.Column(db.Integer, primary_key=True)
    # Individual lesson student (legacy rows always have it). NULL for group lessons.
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), index=True)
    date = db.Column(db.Date, nullable=False, default=today_rome, index=True)
    time = db.Column(db.String(5), nullable=False)
    pagato = db.Column(db.Boolean, nullable=False, default=False)  # legacy, kept untouched

    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), index=True)
    slot_id = db.Column(db.Integer, db.ForeignKey("schedule_slots.id"))
    slot_date = db.Column(db.Date)  # original occurrence date of the slot
    duration_min = db.Column(db.Integer, nullable=False, default=60, server_default="60")
    status = db.Column(db.String(12), nullable=False, default="scheduled", server_default="completed")
    kind = db.Column(db.String(12), nullable=False, default="regular", server_default="regular")
    is_free = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"))

    title = db.Column(db.String(200))
    topics = db.Column(db.Text)
    notes = db.Column(db.Text)
    homework = db.Column(db.Text)
    observations = db.Column(db.Text)

    cancel_reason = db.Column(db.String(255))
    cancelled_at = db.Column(db.DateTime(timezone=True))
    rescheduled_from = db.Column(db.String(16))  # "YYYY-MM-DD HH:MM" of the original slot
    version = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    student = db.relationship("Student")
    group = db.relationship("Group")
    slot = db.relationship("ScheduleSlot")
    attendance = db.relationship(
        "Attendance", back_populates="lesson", lazy="select", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("status IN ('scheduled','completed','cancelled')", name="ck_lessons_status"),
        CheckConstraint("kind IN ('regular','makeup','extra','trial')", name="ck_lessons_kind"),
        Index("uq_lessons_slot_occurrence", "slot_id", "slot_date", unique=True),
        Index("ix_lessons_date_time", "date", "time"),
    )

    is_virtual = False

    @property
    def display_title(self):
        if self.group_id:
            return self.group.name
        if self.student_id:
            return self.student.name
        return "—"

    @property
    def color(self):
        if self.group_id:
            return self.group.color
        return None

    def participants(self):
        """Students expected at this lesson plus anyone with an attendance row."""
        seen = {}
        if self.group_id:
            for s in self.group.members(on=self.date):
                seen[s.id] = s
        elif self.student_id:
            seen[self.student.id] = self.student
        for a in self.attendance:
            seen.setdefault(a.student_id, a.student)
        return sorted(seen.values(), key=lambda s: s.name.lower())

    def attendance_for(self, student_id):
        for a in self.attendance:
            if a.student_id == student_id:
                return a
        return None

    @property
    def end_time(self):
        h, m = (int(x) for x in self.time.split(":"))
        total = h * 60 + m + (self.duration_min or 60)
        total = min(total, 23 * 60 + 59)
        return f"{total // 60:02d}:{total % 60:02d}"


class Attendance(db.Model):
    __tablename__ = "attendance"

    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)
    status = db.Column(db.String(12), nullable=False)
    homework_status = db.Column(db.String(10))
    performance = db.Column(db.SmallInteger)
    note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    lesson = db.relationship("Lesson", back_populates="attendance")
    student = db.relationship("Student")

    __table_args__ = (
        UniqueConstraint("lesson_id", "student_id", name="uq_attendance_lesson_student"),
        CheckConstraint(
            "status IN ('present','late','absent','excused','makeup','cancelled')",
            name="ck_attendance_status",
        ),
        CheckConstraint("performance IS NULL OR performance BETWEEN 1 AND 5", name="ck_attendance_perf"),
    )


# ── Money & credits ──────────────────────────────────────────────────────────

class Package(db.Model):
    __tablename__ = "packages"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    lessons = db.Column(db.Integer, nullable=False)
    price_cents = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("lessons > 0", name="ck_packages_lessons"),
        CheckConstraint("price_cents >= 0", name="ck_packages_price"),
    )


class Charge(db.Model):
    """Lessons sold to a student (a package or N single lessons) = money owed."""

    __tablename__ = "charges"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)
    description = db.Column(db.String(200), nullable=False, default="")
    lessons = db.Column(db.Integer, nullable=False, default=0)
    unit_price_cents = db.Column(db.Integer)
    amount_cents = db.Column(db.Integer, nullable=False)
    package_id = db.Column(db.Integer, db.ForeignKey("packages.id"))
    charged_on = db.Column(db.Date, nullable=False, default=today_rome)
    idempotency_key = db.Column(db.String(64), unique=True)
    voided_at = db.Column(db.DateTime(timezone=True))
    void_reason = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    student = db.relationship("Student")
    package = db.relationship("Package")

    __table_args__ = (
        CheckConstraint("lessons >= 0", name="ck_charges_lessons"),
        CheckConstraint("amount_cents >= 0", name="ck_charges_amount"),
    )


class Payment(db.Model):
    """Money received from (or for) a student."""

    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)
    amount_cents = db.Column(db.Integer, nullable=False)
    method = db.Column(db.String(10), nullable=False, default="cash", server_default="cash")
    paid_on = db.Column(db.Date, nullable=False, default=today_rome, index=True)
    receipt_no = db.Column(db.String(20), unique=True, nullable=False)
    note = db.Column(db.String(255))
    idempotency_key = db.Column(db.String(64), unique=True)
    voided_at = db.Column(db.DateTime(timezone=True))
    void_reason = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    student = db.relationship("Student")

    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_payments_amount"),
        CheckConstraint("method IN ('cash','bank','card','other')", name="ck_payments_method"),
    )


class CreditEntry(db.Model):
    """Append-only lesson-credit ledger.

    remaining lessons = SUM(delta) over all rows of a student.
    A row is never edited except to stamp ``reversed_at`` when a matching
    reversal row is inserted in the same transaction.
    """

    __tablename__ = "credit_entries"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)
    delta = db.Column(db.Integer, nullable=False)
    kind = db.Column(db.String(10), nullable=False)
    charge_id = db.Column(db.Integer, db.ForeignKey("charges.id"))
    attendance_id = db.Column(db.Integer, db.ForeignKey("attendance.id", ondelete="SET NULL"))
    reverses_id = db.Column(db.Integer, db.ForeignKey("credit_entries.id"), unique=True)
    reversed_at = db.Column(db.DateTime(timezone=True))
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    attendance = db.relationship("Attendance")
    charge = db.relationship("Charge")

    __table_args__ = (
        CheckConstraint("delta <> 0", name="ck_credit_delta"),
        CheckConstraint(
            "kind IN ('opening','purchase','consume','adjust','reversal')", name="ck_credit_kind"
        ),
        # Never deduct a lesson twice for the same attendance row.
        Index(
            "uq_credit_consume_active",
            "attendance_id",
            unique=True,
            postgresql_where=text("kind = 'consume' AND reversed_at IS NULL"),
            sqlite_where=text("kind = 'consume' AND reversed_at IS NULL"),
        ),
        # A charge grants its credits once.
        Index(
            "uq_credit_purchase_active",
            "charge_id",
            unique=True,
            postgresql_where=text("kind = 'purchase' AND reversed_at IS NULL"),
            sqlite_where=text("kind = 'purchase' AND reversed_at IS NULL"),
        ),
    )


class Assessment(db.Model):
    __tablename__ = "assessments"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    kind = db.Column(db.String(10), nullable=False, default="test", server_default="test")
    taken_on = db.Column(db.Date, nullable=False, default=today_rome)
    score = db.Column(db.Numeric(6, 2), nullable=False)
    max_score = db.Column(db.Numeric(6, 2), nullable=False)
    note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    student = db.relationship("Student")

    __table_args__ = (
        CheckConstraint("max_score > 0", name="ck_assessments_max"),
        CheckConstraint("score >= 0 AND score <= max_score", name="ck_assessments_score"),
    )

    @property
    def percent(self):
        return float(self.score) / float(self.max_score) * 100 if self.max_score else 0.0
