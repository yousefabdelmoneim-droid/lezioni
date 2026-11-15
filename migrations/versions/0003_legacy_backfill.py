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
lessons = sa.table(
    "lessons",
    sa.column("id", sa.Integer), sa.column("student_id", sa.Integer), sa.column("date", sa.Date),
    sa.column("time", sa.String), sa.column("pagato", sa.Boolean), sa.column("status", sa.String),
    sa.column("kind", sa.String), sa.column("duration_min", sa.Integer), sa.column("version", sa.Integer),
    sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)),
)
attendance = sa.table(
    "attendance",
    sa.column("id", sa.Integer), sa.column("lesson_id", sa.Integer), sa.column("student_id", sa.Integer),
    sa.column("status", sa.String), sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
credit_entries = sa.table(
    "credit_entries",
    sa.column("student_id", sa.Integer), sa.column("delta", sa.Integer), sa.column("kind", sa.String),
    sa.column("attendance_id", sa.Integer), sa.column("note", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
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


def upgrade():
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    today = datetime.now(ZoneInfo("Europe/Rome")).date()

    if bind.execute(sa.select(sa.func.count()).select_from(settings)).scalar() == 0:
        bind.execute(settings.insert().values(id=1, updated_at=now))

    lesson_rows = bind.execute(
        sa.select(lessons.c.id, lessons.c.student_id, lessons.c.date, lessons.c.time, lessons.c.pagato)
        .order_by(lessons.c.date, lessons.c.time, lessons.c.id)
    ).fetchall()

    first_lesson = {}
    paid_past = {}
    for lid, sid, d, t, pagato in lesson_rows:
        if sid is None:
            continue
        first_lesson.setdefault(sid, d)
        past = d <= today
        bind.execute(
            lessons.update().where(lessons.c.id == lid).values(
                status="completed" if past else "scheduled",
                kind="regular", duration_min=60, version=1,
                created_at=now, updated_at=now,
            )
        )
        if not past:
            continue
        att_id = bind.execute(
            attendance.insert()
            .values(lesson_id=lid, student_id=sid, status="present", created_at=now, updated_at=now)
            .returning(attendance.c.id)
        ).scalar_one()
        bind.execute(
            credit_entries.insert().values(
                student_id=sid, delta=-1, kind="consume", attendance_id=att_id, created_at=now
            )
        )
        if pagato:
            paid_past[sid] = paid_past.get(sid, 0) + 1

    for sid, n in paid_past.items():
        bind.execute(
            credit_entries.insert().values(
                student_id=sid, delta=n, kind="opening",
                note="Legacy: lessons marked paid before the upgrade", created_at=now,
            )
        )

    student_rows = bind.execute(sa.select(students.c.id, students.c.lesson_days, students.c.last_reset_date)).fetchall()
    for sid, lesson_days, last_reset in student_rows:
        bind.execute(
            students.update().where(students.c.id == sid).values(
                status="active", billing_mode="postpaid",
                enrollment_date=first_lesson.get(sid) or last_reset or today,
                created_at=now, updated_at=now,
            )
        )
        for weekday, start in _parse_lesson_days(lesson_days):
            bind.execute(
                schedule_slots.insert().values(
                    student_id=sid, weekday=weekday, start_time=start, duration_min=60,
                    valid_from=today, active=True, created_at=now,
                )
            )


def downgrade():
    raise RuntimeError("Downgrade not supported; restore from a Neon backup branch.")
