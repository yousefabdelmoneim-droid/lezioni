"""The upgrade must preserve and correctly convert the original database."""
import os
import tempfile
from datetime import timedelta

from sqlalchemy import create_engine, inspect, text

from lezioni import create_app
from lezioni.boot import ensure_schema
from lezioni.config import TestConfig
from lezioni.extensions import db
from lezioni.models import Lesson, ScheduleSlot, Student
from lezioni.services import billing
from lezioni.utils import today_rome

LEGACY_SQLITE = """
CREATE TABLE students (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL UNIQUE, weekly_plan INTEGER NOT NULL,
                       last_reset_date DATE NOT NULL);
CREATE TABLE lessons (id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id), date DATE NOT NULL,
                      time VARCHAR(5) NOT NULL);
"""


def _legacy_db(url, today):
    """Recreate the original schema exactly as the old app built it (create_all + runtime ALTERs)."""
    eng = create_engine(url)
    with eng.begin() as c:
        for stmt in LEGACY_SQLITE.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        # the old app added these two columns at runtime
        c.execute(text("ALTER TABLE students ADD COLUMN lesson_days VARCHAR(200) DEFAULT ''"))
        c.execute(text("ALTER TABLE lessons ADD COLUMN pagato BOOLEAN NOT NULL DEFAULT false"))
        c.execute(text("INSERT INTO students (id, name, weekly_plan, last_reset_date, lesson_days) VALUES "
                       "(1, 'Marco', 2, :d, 'Lunedì:18:00,Giovedì:17:30'), (2, 'Ahmed', 1, :d, 'Sabato:10:00')"),
                  {"d": today})
        rows = [(1, 40, True), (1, 30, True), (1, 20, False), (1, 10, False), (1, -5, False),
                (2, 14, False), (2, -3, True)]
        for sid, days_ago, paid in rows:
            c.execute(text("INSERT INTO lessons (student_id, date, time, pagato) VALUES (:s, :d, '18:00', :p)"),
                      {"s": sid, "d": today - timedelta(days=days_ago), "p": paid})
    eng.dispose()


def test_legacy_database_is_upgraded_without_losing_data():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    today = today_rome()
    _legacy_db(url, today)

    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = url

    app = create_app(Cfg)
    ensure_schema(app)
    ensure_schema(app)  # idempotent second boot
    with app.app_context():
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0003_legacy_backfill"
        assert Lesson.query.count() == 7
        assert Lesson.query.filter_by(pagato=True).count() == 3  # legacy column untouched
        marco, ahmed = db.session.get(Student, 1), db.session.get(Student, 2)
        am, aa = billing.account(marco), billing.account(ahmed)
        # Marco: 4 past lessons, 2 paid -> 2 owed; the future one is scheduled
        assert (am.purchased, am.consumed, am.remaining) == (2, 4, -2)
        # Ahmed: 1 past unpaid; the future lesson marked paid by the old bug is NOT a prepayment
        assert (aa.purchased, aa.consumed, aa.remaining) == (0, 1, -1)
        assert Lesson.query.filter_by(status="scheduled").count() == 2
        slots = sorted((s.student_id, s.weekday, s.start_time) for s in ScheduleSlot.query.all())
        assert slots == [(1, 0, "18:00"), (1, 3, "17:30"), (2, 5, "10:00")]
        assert marco.status == "active" and marco.billing_mode == "postpaid"
        cols = {c["name"] for c in inspect(db.engine).get_columns("lessons")}
        assert {"pagato", "student_id", "notes", "homework", "status"} <= cols
        db.engine.dispose()
    os.unlink(path)


def test_fresh_database_gets_full_schema():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"

    app = create_app(Cfg)
    ensure_schema(app)
    with app.app_context():
        tables = set(inspect(db.engine).get_table_names())
        assert {"students", "lessons", "attendance", "payments", "charges", "credit_entries", "audit_logs"} <= tables
        db.engine.dispose()
    os.unlink(path)
