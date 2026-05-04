import os
from datetime import date, datetime, timedelta

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, text
from sqlalchemy.orm import subqueryload

from models import GIORNI_SETTIMANA, Lesson, Student, db

# ── App factory ──────────────────────────────────────────────────────────────

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH  = os.path.join(BASE_DIR, "app.db")

# PostgreSQL (Neon) via DATABASE_URL, fallback a SQLite in locale
_db_url = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

app = Flask(__name__)
app.config["SECRET_KEY"]                     = os.environ.get("SECRET_KEY", "cambia-questa-chiave-in-produzione")
app.config["SQLALCHEMY_DATABASE_URI"]        = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"]      = {"pool_pre_ping": True}

db.init_app(app)


def _migrate(app_ctx):
    """Non-destructive schema migration: add columns that may be missing."""
    with app_ctx:
        db.create_all()
        with db.engine.connect() as conn:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(students)")).fetchall()]
            if "lesson_days" not in cols:
                conn.execute(text("ALTER TABLE students ADD COLUMN lesson_days VARCHAR(200) DEFAULT ''"))
                conn.commit()


_migrate(app.app_context())


# ── Helpers ──────────────────────────────────────────────────────────────────

def _week_bounds():
    today      = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end   = week_start + timedelta(days=6)
    return week_start, week_end


def _all_students():
    """Students with lessons eagerly loaded — prevents N+1 on property access."""
    return (
        Student.query
        .options(subqueryload(Student.lessons))
        .order_by(func.lower(Student.name))
        .all()
    )


def _parse_days(form, weekly_plan):
    """Extract and validate lesson_days from form data."""
    day1 = form.get("lesson_day_1", "").strip()
    day2 = form.get("lesson_day_2", "").strip()
    days = []
    if day1 in GIORNI_SETTIMANA:
        days.append(day1)
    if weekly_plan == 2 and day2 in GIORNI_SETTIMANA:
        if day2 == day1:
            return None, "Scegli due giorni diversi per le lezioni settimanali."
        days.append(day2)
    return ",".join(days), None


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    students      = _all_students()
    today         = date.today()
    now_time      = datetime.now().strftime("%H:%M")
    week_start, _ = _week_bounds()

    recent_lessons = (
        Lesson.query
        .join(Student)
        .filter(Lesson.date >= week_start)
        .order_by(Lesson.date.desc(), Lesson.time.desc())
        .all()
    )

    return render_template(
        "index.html",
        students=students,
        today=today,
        now_time=now_time,
        recent_lessons=recent_lessons,
        giorni=GIORNI_SETTIMANA,
        view="dashboard",
    )


# ── Student management ───────────────────────────────────────────────────────

@app.route("/studenti/aggiungi", methods=["POST"])
def add_student():
    name = request.form.get("name", "").strip()

    try:
        weekly_plan = int(request.form.get("weekly_plan", 1))
    except ValueError:
        weekly_plan = 1

    if not name:
        flash("Il nome dello studente non può essere vuoto.", "error")
        return redirect(url_for("index"))

    if weekly_plan not in (1, 2):
        flash("Le lezioni settimanali devono essere 1 o 2.", "error")
        return redirect(url_for("index"))

    existing = Student.query.filter(func.lower(Student.name) == func.lower(name)).first()
    if existing:
        flash(f'Esiste già uno studente di nome "{existing.name}".', "error")
        return redirect(url_for("index"))

    lesson_days, err = _parse_days(request.form, weekly_plan)
    if err:
        flash(err, "error")
        return redirect(url_for("index"))

    try:
        student = Student(
            name=name,
            weekly_plan=weekly_plan,
            lesson_days=lesson_days or "",
            last_reset_date=date.today(),
        )
        db.session.add(student)
        db.session.commit()
        flash(f'Studente "{name}" aggiunto con successo.', "success")
    except Exception:
        db.session.rollback()
        flash("Errore durante il salvataggio. Riprova.", "error")

    return redirect(url_for("index"))


@app.route("/studenti/<int:student_id>/elimina", methods=["POST"])
def delete_student(student_id):
    student = Student.query.get_or_404(student_id)
    name = student.name
    try:
        db.session.delete(student)
        db.session.commit()
        flash(f'Studente "{name}" e tutte le sue lezioni sono stati eliminati.', "success")
    except Exception:
        db.session.rollback()
        flash("Errore durante l'eliminazione.", "error")
    return redirect(url_for("index"))


@app.route("/studenti/<int:student_id>/azzera", methods=["POST"])
def reset_counter(student_id):
    """AJAX endpoint — cancella tutte le lezioni dello studente (segna come pagato)."""
    student = Student.query.get_or_404(student_id)
    try:
        Lesson.query.filter_by(student_id=student_id).delete()
        db.session.commit()
        return jsonify({"ok": True, "total": 0})
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "Errore durante il reset."}), 500


# ── Lessons ───────────────────────────────────────────────────────────────────

@app.route("/lezioni/aggiungi", methods=["POST"])
def add_lesson():
    try:
        student_id = int(request.form.get("student_id", 0))
    except ValueError:
        flash("Studente non valido.", "error")
        return redirect(url_for("lessons_view"))

    date_str = request.form.get("date", "").strip()
    time_str = request.form.get("time", "").strip()
    student  = Student.query.get_or_404(student_id)

    try:
        lesson_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Formato data non valido.", "error")
        return redirect(url_for("lessons_view"))

    if not time_str or len(time_str) != 5:
        flash("Formato orario non valido (usa HH:MM).", "error")
        return redirect(url_for("lessons_view"))

    try:
        lesson = Lesson(student_id=student_id, date=lesson_date, time=time_str)
        db.session.add(lesson)
        db.session.commit()
        flash(
            f'Lezione registrata per "{student.name}" il '
            f'{lesson_date.strftime("%-d %b %Y")} alle {time_str}.',
            "success",
        )
    except Exception:
        db.session.rollback()
        flash("Errore durante il salvataggio della lezione.", "error")

    return redirect(url_for("lessons_view"))


@app.route("/lezioni/rapida/<int:student_id>", methods=["POST"])
def quick_add_lesson(student_id):
    student = Student.query.get_or_404(student_id)
    now     = datetime.now()
    try:
        lesson = Lesson(student_id=student_id, date=now.date(), time=now.strftime("%H:%M"))
        db.session.add(lesson)
        db.session.commit()
        flash(f'Lezione rapida registrata per "{student.name}" alle {lesson.time}.', "success")
    except Exception:
        db.session.rollback()
        flash("Errore durante la registrazione rapida.", "error")
    return redirect(url_for("index"))


@app.route("/lezioni/<int:lesson_id>/elimina", methods=["POST"])
def delete_lesson(lesson_id):
    lesson = Lesson.query.get_or_404(lesson_id)
    try:
        db.session.delete(lesson)
        db.session.commit()
        flash("Lezione eliminata.", "success")
    except Exception:
        db.session.rollback()
        flash("Errore durante l'eliminazione.", "error")
    return redirect(request.referrer or url_for("lessons_view"))


# ── Lessons view ─────────────────────────────────────────────────────────────

@app.route("/storico")
def lessons_view():
    students       = _all_students()
    today          = date.today()
    now_time       = datetime.now().strftime("%H:%M")
    week_start, _  = _week_bounds()

    filter_mode    = request.args.get("filter", "all")
    student_filter = request.args.get("student", "all")

    query = Lesson.query.join(Student)

    if filter_mode == "week":
        query = query.filter(Lesson.date >= week_start, Lesson.date <= today)

    if student_filter != "all":
        try:
            query = query.filter(Lesson.student_id == int(student_filter))
        except ValueError:
            pass

    lessons = query.order_by(Lesson.date.desc(), Lesson.time.desc()).all()

    return render_template(
        "index.html",
        students=students,
        today=today,
        now_time=now_time,
        lessons=lessons,
        filter_mode=filter_mode,
        student_filter=student_filter,
        giorni=GIORNI_SETTIMANA,
        view="lessons",
    )


# ── Upcoming view ─────────────────────────────────────────────────────────────

@app.route("/prossime")
def upcoming_view():
    students = _all_students()
    today    = date.today()
    now_time = datetime.now().strftime("%H:%M")
    now      = datetime.now()

    # FIX: filtra per data+ora, non solo per data
    # Una lezione alle 9:00 non resta in "Prossime" alle 22:00
    upcoming = (
        Lesson.query
        .join(Student)
        .filter(
            (Lesson.date > today) |
            ((Lesson.date == today) & (Lesson.time >= now.strftime("%H:%M")))
        )
        .order_by(Lesson.date.asc(), Lesson.time.asc())
        .all()
    )

    return render_template(
        "index.html",
        students=students,
        today=today,
        now_time=now_time,
        upcoming=upcoming,
        giorni=GIORNI_SETTIMANA,
        view="upcoming",
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
