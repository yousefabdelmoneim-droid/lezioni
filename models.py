from datetime import date, timedelta
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

GIORNI_SETTIMANA = [
    "Lunedì", "Martedì", "Mercoledì",
    "Giovedì", "Venerdì", "Sabato", "Domenica",
]


class Student(db.Model):
    __tablename__ = "students"

    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(120), unique=True, nullable=False)
    weekly_plan     = db.Column(db.Integer, nullable=False, default=1)
    lesson_days     = db.Column(db.String(200), nullable=False, server_default="")
    last_reset_date = db.Column(db.Date, nullable=False, default=date.today)

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
        return sum(1 for l in self.lessons if l.date >= self.last_reset_date)

    @property
    def lessons_this_week(self):
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        return sum(1 for l in self.lessons if week_start <= l.date <= today)

    @property
    def weekly_warning(self):
        return self.lessons_this_week < self.weekly_plan

    def __repr__(self):
        return f"<Student {self.name!r}>"


class Lesson(db.Model):
    __tablename__ = "lessons"

    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    date       = db.Column(db.Date, nullable=False, default=date.today)
    time       = db.Column(db.String(5), nullable=False)

    def __repr__(self):
        return f"<Lesson sid={self.student_id} {self.date} {self.time}>"
