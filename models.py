import sys
from datetime import date, timedelta

from flask_sqlalchemy import SQLAlchemy

# Python >= 3.9: zoneinfo è built-in, non serve backports
if sys.version_info >= (3, 9):
    from zoneinfo import ZoneInfo
else:
    from backports.zoneinfo import ZoneInfo

from datetime import datetime

db = SQLAlchemy()

ROME = ZoneInfo("Europe/Rome")

GIORNI_SETTIMANA = [
    "Lunedì", "Martedì", "Mercoledì",
    "Giovedì", "Venerdì", "Sabato", "Domenica",
]


def oggi_rome():
    return datetime.now(ROME).date()


class Student(db.Model):
    __tablename__ = "students"

    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(120), unique=True, nullable=False)
    weekly_plan     = db.Column(db.Integer, nullable=False, default=1)
    lesson_days     = db.Column(db.String(200), nullable=False, server_default="")
    last_reset_date = db.Column(db.Date, nullable=False, default=oggi_rome)

    lessons = db.relationship(
        "Lesson",
        backref="student",
        lazy="select",
        cascade="all, delete-orphan",
    )

    @property
    def lesson_days_list(self):
        if not self.lesson_days:
            return []
        return [d.strip() for d in self.lesson_days.split(",") if d.strip()]

    @property
    def lessons_since_reset(self):
        """Conta solo lezioni NON pagate (pagato == False)."""
        return sum(
            1 for l in self.lessons
            if l.date >= self.last_reset_date and not l.pagato
        )

    @property
    def lessons_this_week(self):
        today = oggi_rome()
        week_start = today - timedelta(days=today.weekday())
        return sum(
            1 for l in self.lessons
            if week_start <= l.date <= today and not l.pagato
        )

    def __repr__(self):
        return f"<Student {self.name!r}>"


class Lesson(db.Model):
    __tablename__ = "lessons"

    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    date       = db.Column(db.Date, nullable=False, default=oggi_rome)
    time       = db.Column(db.String(5), nullable=False)
    pagato     = db.Column(db.Boolean, nullable=False, default=False)

    def __repr__(self):
        return f"<Lesson sid={self.student_id} {self.date} {self.time} pagato={self.pagato}>"
