from datetime import timedelta

from flask import Blueprint, abort, redirect, render_template, request, url_for
from sqlalchemy import func

from ..extensions import db
from ..models import LESSON_KINDS, Group, Lesson, ScheduleSlot, Settings, Student
from ..services import attendance as attendance_svc
from ..services import audit, billing, schedule, whatsapp
from ..services.attendance import AttendanceError
from ..utils import now_rome, parse_date, today_rome, valid_time, week_start
from ._helpers import back, flash_error, flash_ok, form_int, form_str, get_or_404, uid

bp = Blueprint("lessons", __name__)


# ── Agenda & calendar ────────────────────────────────────────────────────────

@bp.route("/lessons")
def agenda():
    view = request.args.get("view", "week")
    day = parse_date(request.args.get("date")) or today_rome()
    if view == "day":
        start = end = day
        prev_d, next_d = day - timedelta(days=1), day + timedelta(days=1)
    else:
        view = "week"
        start = week_start(day)
        end = start + timedelta(days=6)
        prev_d, next_d = start - timedelta(days=7), start + timedelta(days=7)
    items = schedule.agenda(start, end)
    days = {}
    d = start
    while d <= end:
        days[d] = []
        d += timedelta(days=1)
    for it in items:
        days.setdefault(it.date, []).append(it)
    return render_template(
        "lessons/agenda.html", days=days, view=view, day=day, start=start, end=end,
        prev_d=prev_d, next_d=next_d, nav="lessons",
    )


@bp.route("/calendar")
def calendar():
    return render_template("lessons/calendar.html", nav="calendar")


# ── Create / open ────────────────────────────────────────────────────────────

@bp.route("/lessons/new", methods=["GET", "POST"])
def new():
    settings = Settings.get()
    groups = Group.query.filter_by(active=True).order_by(func.lower(Group.name)).all()
    students = Student.query.filter_by(status="active").order_by(func.lower(Student.name)).all()
    form = {
        "target": request.values.get("target", "group" if groups else "student"),
        "group_id": request.values.get("group_id", type=int),
        "student_id": request.values.get("student_id", type=int),
        "date": request.values.get("date") or today_rome().isoformat(),
        "time": request.values.get("time") or now_rome().strftime("%H:00"),
        "duration_min": request.values.get("duration_min", type=int) or settings.default_duration_min,
        "kind": request.values.get("kind", "regular"),
        "title": request.values.get("title", ""),
    }
    conflicts = []
    if request.method == "POST":
        d = parse_date(form["date"])
        t = valid_time(form["time"])
        dur = form_int("duration_min", None, 15, 600)
        kind = form["kind"] if form["kind"] in LESSON_KINDS else "regular"
        group = db.session.get(Group, form["group_id"]) if form["target"] == "group" and form["group_id"] else None
        student = db.session.get(Student, form["student_id"]) if form["target"] == "student" and form["student_id"] else None
        if not d or not t or not dur:
            flash_error("err_invalid_datetime")
        elif group is None and student is None:
            flash_error("err_choose_target")
        else:
            conflicts = schedule.conflicts(d, t, dur)
            if conflicts and not request.form.get("confirm_conflict"):
                flash_error("warn_conflict")
            else:
                lesson = Lesson(
                    group_id=group.id if group else None,
                    student_id=student.id if student else None,
                    date=d, time=t, duration_min=dur, kind=kind, status="scheduled",
                    title=form_str("title", 200) or None,
                )
                db.session.add(lesson)
                db.session.flush()
                audit.log("lesson.create", "lesson", lesson.id, student_id=lesson.student_id,
                          summary=f"{lesson.display_title} {d} {t}")
                db.session.commit()
                flash_ok("msg_lesson_created")
                return redirect(url_for("lessons.show", lesson_id=lesson.id))
    return render_template(
        "lessons/new.html", form=form, groups=groups, students=students, conflicts=conflicts,
        kinds=LESSON_KINDS, nav="lessons",
    )


@bp.route("/lessons/open-slot", methods=["POST"])
def open_slot():
    slot = get_or_404(ScheduleSlot, form_int("slot_id", 0))
    on = parse_date(form_str("date"))
    if on is None:
        abort(400)
    lesson, _created = schedule.materialize(slot, on, user_id=uid())
    return redirect(url_for("lessons.show", lesson_id=lesson.id))


@bp.route("/lessons/quick/<int:student_id>", methods=["POST"])
def quick(student_id):
    """'Adesso' from the old app: an individual lesson right now, student present."""
    student = get_or_404(Student, student_id)
    now = now_rome()
    lesson = Lesson(student_id=student.id, date=now.date(), time=now.strftime("%H:%M"),
                    duration_min=Settings.get().default_duration_min, status="scheduled", kind="regular")
    db.session.add(lesson)
    db.session.flush()
    audit.log("lesson.create", "lesson", lesson.id, student_id=student.id, summary=f"{student.name} · {lesson.date} {lesson.time}")
    attendance_svc.mark(lesson, [{"student_id": student.id, "status": "present"}], user_id=uid())
    flash_ok("msg_quick_lesson", name=student.name, time=lesson.time)
    return back("dashboard.index")


# ── Lesson page: notebook + attendance ───────────────────────────────────────

@bp.route("/lessons/<int:lesson_id>")
def show(lesson_id):
    settings = Settings.get()
    lesson = get_or_404(Lesson, lesson_id)
    participants = lesson.participants()
    accounts = billing.accounts_for(participants, settings=settings) if participants else {}
    consumed = {
        a.id for a in lesson.attendance if billing.active_consumption(a) is not None
    }
    previous = None
    if lesson.group_id or lesson.student_id:
        q = Lesson.query.filter(Lesson.id != lesson.id, Lesson.status != "cancelled",
                                (Lesson.date < lesson.date) | ((Lesson.date == lesson.date) & (Lesson.time < lesson.time)))
        q = q.filter(Lesson.group_id == lesson.group_id) if lesson.group_id else q.filter(Lesson.student_id == lesson.student_id)
        previous = q.order_by(Lesson.date.desc(), Lesson.time.desc()).first()
    wa_links = {}
    for s in participants:
        num = whatsapp.recipient(s, settings)
        if not num:
            continue
        a = lesson.attendance_for(s.id)
        if a is not None and a.status in ("absent", "excused"):
            wa_links[s.id] = ("absence", whatsapp.link(num, whatsapp.message("absence", s, settings, lesson=lesson)))
        elif lesson.homework:
            wa_links[s.id] = ("homework", whatsapp.link(num, whatsapp.message("homework", s, settings, homework=lesson.homework)))
    all_students = Student.query.filter(Student.status != "archived").order_by(func.lower(Student.name)).all()
    return render_template(
        "lessons/show.html", lesson=lesson, participants=participants, accounts=accounts, consumed=consumed,
        previous=previous, wa_links=wa_links, kinds=LESSON_KINDS, all_students=all_students, nav="lessons",
    )


@bp.route("/lessons/<int:lesson_id>/notebook", methods=["POST"])
def save_notebook(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    for field in ("title", "topics", "notes", "homework", "observations"):
        if field in request.form:
            setattr(lesson, field, form_str(field, 200 if field == "title" else 20000) or None)
    kind = form_str("kind")
    if kind in LESSON_KINDS:
        lesson.kind = kind
    lesson.version = (lesson.version or 1) + 1
    audit.log("lesson.notebook", "lesson", lesson.id)
    db.session.commit()
    flash_ok("msg_saved")
    return redirect(url_for("lessons.show", lesson_id=lesson.id))


@bp.route("/lessons/<int:lesson_id>/attendance", methods=["POST"])
def save_attendance(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    entries = []
    for sid in request.form.getlist("student_ids"):
        try:
            sid = int(sid)
        except ValueError:
            continue
        entries.append({
            "student_id": sid,
            "status": request.form.get(f"status_{sid}"),
            "homework_status": request.form.get(f"hw_{sid}"),
            "performance": request.form.get(f"perf_{sid}"),
            "note": request.form.get(f"note_{sid}"),
        })
    try:
        attendance_svc.mark(lesson, entries, user_id=uid())
    except AttendanceError as e:
        flash_error("err_" + e.code)
        return redirect(url_for("lessons.show", lesson_id=lesson.id))
    flash_ok("msg_attendance_saved")
    return redirect(url_for("lessons.show", lesson_id=lesson.id))


@bp.route("/lessons/<int:lesson_id>/guest", methods=["POST"])
def add_guest(lesson_id):
    """Add a student who is not a member (e.g. a makeup from another group)."""
    lesson = get_or_404(Lesson, lesson_id)
    student = get_or_404(Student, form_int("student_id", 0))
    try:
        attendance_svc.mark(lesson, [{"student_id": student.id, "status": "makeup"}], user_id=uid())
    except AttendanceError as e:
        flash_error("err_" + e.code)
    return redirect(url_for("lessons.show", lesson_id=lesson.id))


@bp.route("/lessons/<int:lesson_id>/cancel", methods=["POST"])
def cancel(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    attendance_svc.cancel_lesson(lesson, form_str("reason", 255), user_id=uid())
    flash_ok("msg_lesson_cancelled")
    return redirect(url_for("lessons.show", lesson_id=lesson.id))


@bp.route("/lessons/<int:lesson_id>/restore", methods=["POST"])
def restore(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    attendance_svc.restore_lesson(lesson, user_id=uid())
    flash_ok("msg_saved")
    return redirect(url_for("lessons.show", lesson_id=lesson.id))


@bp.route("/lessons/<int:lesson_id>/free", methods=["POST"])
def toggle_free(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    attendance_svc.set_free(lesson, not lesson.is_free, user_id=uid())
    flash_ok("msg_saved")
    return redirect(url_for("lessons.show", lesson_id=lesson.id))


@bp.route("/lessons/<int:lesson_id>/reschedule", methods=["POST"])
def reschedule(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    d = parse_date(form_str("date"))
    t = valid_time(form_str("time"))
    dur = form_int("duration_min", lesson.duration_min, 15, 600)
    if not d or not t:
        flash_error("err_invalid_datetime")
        return redirect(url_for("lessons.show", lesson_id=lesson.id))
    found = schedule.conflicts(d, t, dur, exclude_lesson_id=lesson.id)
    if found and not request.form.get("confirm_conflict"):
        flash_error("warn_conflict_named", items=", ".join(f"{i.title if i.is_virtual else i.display_title} {i.time}" for i in found))
        return redirect(url_for("lessons.show", lesson_id=lesson.id, reschedule=1, date=d.isoformat(), time=t))
    if not lesson.rescheduled_from:
        lesson.rescheduled_from = f"{lesson.date.isoformat()} {lesson.time}"
    old = f"{lesson.date} {lesson.time}"
    lesson.date, lesson.time, lesson.duration_min = d, t, dur
    audit.log("lesson.reschedule", "lesson", lesson.id, student_id=lesson.student_id, summary=f"{lesson.display_title} · {old} → {d} {t}")
    db.session.commit()
    flash_ok("msg_lesson_rescheduled")
    return redirect(url_for("lessons.show", lesson_id=lesson.id))


@bp.route("/lessons/<int:lesson_id>/delete", methods=["POST"])
def delete(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    if lesson.attendance:
        flash_error("err_lesson_has_attendance")
        return redirect(url_for("lessons.show", lesson_id=lesson.id))
    audit.log("lesson.delete", "lesson", lesson.id, student_id=lesson.student_id,
              summary=f"{lesson.display_title} {lesson.date} {lesson.time}")
    db.session.delete(lesson)
    db.session.commit()
    flash_ok("msg_deleted")
    return redirect(url_for("lessons.agenda"))


# ── Recurring slots ──────────────────────────────────────────────────────────

@bp.route("/schedule/slots", methods=["POST"])
def add_slot():
    group_id = form_int("group_id")
    student_id = form_int("student_id")
    weekday = form_int("weekday", None, 0, 6)
    t = valid_time(form_str("start_time"))
    dur = form_int("duration_min", Settings.get().default_duration_min, 15, 600)
    owner_group = db.session.get(Group, group_id) if group_id else None
    owner_student = db.session.get(Student, student_id) if student_id and not owner_group else None
    if weekday is None or not t or (owner_group is None and owner_student is None):
        flash_error("err_invalid_slot")
        return back("dashboard.index")
    found = schedule.slot_conflicts(weekday, t, dur)
    slot = ScheduleSlot(
        group_id=owner_group.id if owner_group else None,
        student_id=owner_student.id if owner_student else None,
        weekday=weekday, start_time=t, duration_min=dur,
        valid_from=parse_date(form_str("valid_from")) or today_rome(),
    )
    db.session.add(slot)
    db.session.flush()
    audit.log("slot.create", "slot", slot.id, student_id=slot.student_id, summary=f"{slot.title} · {t}")
    db.session.commit()
    if found:
        flash_error("warn_slot_conflict", items=", ".join(f"{s.title} {s.start_time}" for s in found))
    else:
        flash_ok("msg_saved")
    return back("dashboard.index")


@bp.route("/schedule/slots/<int:slot_id>/end", methods=["POST"])
def end_slot(slot_id):
    slot = get_or_404(ScheduleSlot, slot_id)
    slot.active = False
    slot.valid_until = today_rome()
    audit.log("slot.end", "slot", slot.id, student_id=slot.student_id, summary=f"{slot.title} · {slot.start_time}")
    db.session.commit()
    flash_ok("msg_saved")
    return back("dashboard.index")
