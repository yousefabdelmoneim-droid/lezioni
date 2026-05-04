import sys
from datetime import date, timedelta
from flask_sqlalchemy import SQLAlchemy

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

GIORNI_WEEKDAY = {
    "Lunedì": 0, "Martedì": 1, "Mercoledì": 2,
    "Giovedì": 3, "Venerdì": 4, "Sabato": 5, "Domenica": 6,
}

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
        """Restituisce lista di dict: [{"day": "Lunedì", "time": "18:00"}, ...]"""
        if not self.lesson_days:
            return []
        result = []
        for entry in self.lesson_days.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                parts = entry.split(":")
                # formato "Lunedì:18:00" → parts = ["Lunedì", "18", "00"]
                day = parts[0]
                time = f"{parts[1]}:{parts[2]}" if len(parts) >= 3 else ""
                result.append({"day": day, "time": time})
            else:
                result.append({"day": entry, "time": ""})
        return result

    @property
    def lessons_since_reset(self):
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

    def upcoming_recurring(self, weeks=8):
        """Genera le prossime lezioni ricorrenti per le settimane future."""
        today = oggi_rome()
        now_time = datetime.now(ROME).strftime("%H:%M")
        events = []

        for entry in self.lesson_days_list:
            day_name = entry["day"]
            time_str = entry["time"]
            if day_name not in GIORNI_WEEKDAY or not time_str:
                continue

            target_weekday = GIORNI_WEEKDAY[day_name]

            for week_offset in range(weeks):
                # calcola il lunedì della settimana corrente + offset
                monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
                lesson_date = monday + timedelta(days=target_weekday)

                # salta lezioni passate (oggi incluso se l'ora è già passata)
                if lesson_date < today:
                    continue
                if lesson_date == today and time_str <= now_time:
                    continue

                events.append({
                    "student_id": self.id,
                    "student_name": self.name,
                    "date": lesson_date,
                    "time": time_str,
                })

        return events

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
