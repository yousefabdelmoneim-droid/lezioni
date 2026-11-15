"""Safe database bootstrapping.

* Fresh database: run every migration.
* Original database (tables present, no ``alembic_version``): make sure the
  columns the old app added at runtime exist, *stamp* the baseline revision,
  then run the new migrations.
* Already migrated: run only pending migrations (usually none).

On PostgreSQL an advisory lock prevents two processes from migrating at once.
All pending migrations run in a single transaction on PostgreSQL, so a failure
leaves the database exactly as it was.
"""
import logging
import os

from flask_migrate import stamp, upgrade
from sqlalchemy import inspect, text

from .extensions import db

log = logging.getLogger(__name__)
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")
BASELINE = "0001_baseline"
LOCK_ID = 727274


def _patch_legacy_columns(conn):
    insp = inspect(conn)
    student_cols = {c["name"] for c in insp.get_columns("students")}
    if "lesson_days" not in student_cols:
        conn.execute(text("ALTER TABLE students ADD COLUMN lesson_days VARCHAR(200) NOT NULL DEFAULT ''"))
    lesson_cols = {c["name"] for c in insp.get_columns("lessons")}
    if "pagato" not in lesson_cols:
        conn.execute(text("ALTER TABLE lessons ADD COLUMN pagato BOOLEAN NOT NULL DEFAULT false"))


def ensure_schema(app):
    with app.app_context():
        engine = db.engine
        is_pg = engine.dialect.name == "postgresql"
        lock_conn = engine.connect() if is_pg else None
        try:
            if lock_conn is not None:
                lock_conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": LOCK_ID})
            tables = set(inspect(engine).get_table_names())
            if "alembic_version" not in tables and "students" in tables:
                log.warning("Original schema detected: stamping baseline before migrating.")
                with engine.begin() as conn:
                    _patch_legacy_columns(conn)
                stamp(directory=MIGRATIONS_DIR, revision=BASELINE)
            upgrade(directory=MIGRATIONS_DIR)
        finally:
            if lock_conn is not None:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})
                lock_conn.close()
