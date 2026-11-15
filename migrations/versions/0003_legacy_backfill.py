"""Convert the original data to the new model (no row is deleted or changed in the old columns).

Rules agreed with the owner:

* every past lesson (date <= today, Europe/Rome) becomes a *completed* lesson
  with the student marked *present*, consuming one lesson credit;
* past lessons marked ``pagato`` become *opening credits* for the student, so
  unpaid past lessons show as owed lessons (negative balance);
* future lessons stay *scheduled*; their ``pagato`` flag (set by the old
  "Pagato" button bug) is NOT counted as a prepayment;
* ``students.lesson_days`` ("Lunedì:18:00,Venerdì:20:00") becomes weekly
  schedule slots, starting today;
* students start as active, pay-after ("postpaid").

Revision ID: 0003_legacy_backfill
Revises: 0002_platform_schema
Create Date: 2026-11-15
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

revision = "0003_legacy_backfill"
down_revision = "0002_platform_schema"
branch_labels = None
depends_on = None

WEEKDAYS = {
    "lunedì": 0, "lunedi": 0, "martedì": 1, "martedi": 1, "mercoledì": 2, "mercoledi": 2,
    "giovedì": 3, "giovedi": 3, "venerdì": 4, "venerdi": 4, "sabato": 5, "domenica": 6,
}

students = sa.table(
    "students",
    sa.column("id", sa.Integer), sa.column("lesson_days", sa.String), sa.column("last_reset_date", sa.Date),
    sa.column("status", sa.String), sa.column("billing_mode", sa.String),
    sa.column("enrollment_date", sa.Date), sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
schedule_slots = sa.table(
    "schedule_slots",
    sa.column("student_id", sa.Integer), sa.column("weekday", sa.Integer), sa.column("start_time", sa.String),
    sa.column("duration_min", sa.Integer), sa.column("valid_from", sa.Date), sa.column("active", sa.Boolean),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
settings = sa.table("settings", sa.column("id", sa.Integer), sa.column("updated_at", sa.DateTime(timezone=True)))


def _parse_lesson_days(value):
    """'Lunedì:18:00,Venerdì:20:00' -> [(0, '18:00'), (4, '20:00')]"""
    out = []
    for entry in (value or "").split(","):
        parts = entry.strip().split(":")
        if len(parts) < 3:
            continue
        day = WEEKDAYS.get(parts[0].strip().lower())
        try:
            h, m = int(parts[1]), int(parts[2])
        except ValueError:
            continue
        if day is None or not (0 <= h <= 23 and 0 <= m <= 59):
            continue
        out.append((day, f"{h:02d}:{m:02d}"))
    return out


_TYPES = {"today": sa.Date(), "now": sa.DateTime(timezone=True), "yes": sa.Boolean()}


def _sql(statement):
    """text() with typed parameters, so dates/booleans are stored correctly on SQLite and PostgreSQL."""
    used = [name for name in _TYPES if f":{name}" in statement]
    return sa.text(statement).bindparams(*(sa.bindparam(n, type_=_TYPES[n]) for n in used))


def upgrade():
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    today = datetime.now(ZoneInfo("Europe/Rome")).date()

    if bind.execute(sa.select(sa.func.count()).select_from(settings)).scalar() == 0:
        bind.execute(settings.insert().values(id=1, updated_at=now))

    # Set-based statements only: the number of round trips to the database is
    # constant, however many lessons exist (the app and Neon are in different regions).
    values = {"today": today, "now": now, "yes": True}

    # 1. lessons: past -> completed, future -> scheduled
    bind.execute(_sql(
        "UPDATE lessons SET status = CASE WHEN date <= :today THEN 'completed' ELSE 'scheduled' END, "
        "kind = 'regular', duration_min = 60, version = 1, created_at = :now, updated_at = :now"
    ), values)

    # 2. every past lesson: the student was present
    bind.execute(_sql(
        "INSERT INTO attendance (lesson_id, student_id, status, created_at, updated_at) "
        "SELECT id, student_id, 'present', :now, :now FROM lessons "
        "WHERE date <= :today AND student_id IS NOT NULL"
    ), values)

    # 3. ...and used one lesson credit
    bind.execute(_sql(
        "INSERT INTO credit_entries (student_id, delta, kind, attendance_id, created_at) "
        "SELECT student_id, -1, 'consume', id, :now FROM attendance"
    ), values)

    # 4. past lessons marked paid -> opening credits (future ones are not prepayments)
    bind.execute(_sql(
        "INSERT INTO credit_entries (student_id, delta, kind, note, created_at) "
        "SELECT student_id, COUNT(*), 'opening', 'Legacy: lessons marked paid before the upgrade', :now "
        "FROM lessons WHERE date <= :today AND pagato = :yes AND student_id IS NOT NULL "
        "GROUP BY student_id"
    ), values)

    # 5. students: active, pay-after, enrolled since their first lesson
    bind.execute(_sql(
        "UPDATE students SET status = 'active', billing_mode = 'postpaid', created_at = :now, updated_at = :now, "
        "enrollment_date = COALESCE("
        "(SELECT MIN(l.date) FROM lessons l WHERE l.student_id = students.id), last_reset_date, :today)"
    ), values)

    # 6. weekly slots from the old "lesson_days" text (one multi-row insert)
    student_rows = bind.execute(sa.select(students.c.id, students.c.lesson_days)).fetchall()
    slot_rows = [
        {"student_id": sid, "weekday": weekday, "start_time": start, "duration_min": 60,
         "valid_from": today, "active": True, "created_at": now}
        for sid, lesson_days in student_rows
        for weekday, start in _parse_lesson_days(lesson_days)
    ]
    if slot_rows:
        bind.execute(schedule_slots.insert(), slot_rows)


def downgrade():
    raise RuntimeError("Downgrade not supported; restore from a Neon backup branch.")
