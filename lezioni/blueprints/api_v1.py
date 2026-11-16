"""JSON API for the Android app (`/api/v1`).

Authentication: `POST /api/v1/auth/login` returns a Bearer token; every other
call sends `Authorization: Bearer <token>`. No cookies, so no CSRF. All money
values are integer cents, all dates ISO 8601. Errors are
`{"ok": false, "error": "<code>"}` with an HTTP status.

Business rules live in the existing services (billing, attendance, schedule),
so the website and the app always behave the same way on the same data.
"""
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, g, jsonify, request
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from .. import serializers as ser
from ..extensions import db
from ..models import (
    ASSESSMENT_KINDS, LESSON_KINDS, PAYMENT_METHODS, Assessment, Attendance, Charge, CreditEntry, Group,
    GroupMember, Lesson, Package, Payment, ScheduleSlot, Settings, Student, as_utc,
)
from ..services import attendance as attendance_svc
from ..services import audit, billing, schedule, stats, tokens, whatsapp
from ..services.attendance import AttendanceError
from ..services.billing import BillingError
from ..utils import month_start, now_rome, parse_date, today_rome, valid_time, week_start
from .groups import COLORS

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

API_VERSION = 1


# ── helpers ──────────────────────────────────────────────────────────────────

class ApiError(Exception):
    def __init__(self, code, status=400, **extra):
        super().__init__(code)
        self.code, self.status, self.extra = code, status, extra


@bp.errorhandler(ApiError)
def _api_error(e):
    body = {"ok": False, "error": e.code}
    body.update(e.extra)
    return jsonify(body), e.status


def ok(**data):
    return jsonify({"ok": True, **data})


def body():
    data = request.get_json(silent=True)
    if data is None:
        if request.method in ("POST", "PUT", "PATCH") and request.content_length:
            raise ApiError("invalid_json")
        return {}
    if not isinstance(data, dict):
        raise ApiError("invalid_json")
    return data


def get_or_404(model, ident):
    obj = db.session.get(model, ident)
    if obj is None:
        raise ApiError("not_found", 404)
    return obj


def text(data, key, limit, default=None):
    value = data.get(key)
    if value is None:
        return default
    value = str(value).strip()
    return value[:limit] if value else default


def integer(data, key, default=None, lo=None, hi=None):
    value = data.get(key, default)
    if value is None or value == "":
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ApiError("invalid_number", field=key)
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ApiError("invalid_number", field=key)
    return value


def uid():
    return current_user.id if current_user.is_authenticated else None


# ── health & auth ────────────────────────────────────────────────────────────

@bp.route("/health")
def health():
    return ok(version=API_VERSION, server_time=now_rome().isoformat())


@bp.route("/auth/login", methods=["POST"])
def login():
    from .auth import authenticate

    data = body()
    user, error = authenticate(data.get("username"), data.get("password"), request.remote_addr or "")
    if user is None:
        raise ApiError("login_" + error, 429 if error == "locked" else 401)
    token, row = tokens.issue(user, text(data, "device_name", 120, "Android"))
    audit.log("auth.api_login", "user", user.id, user_id=user.id, summary=row.name)
    db.session.commit()
    return ok(token=token, expires_at=as_utc(row.expires_at).isoformat(),
              user={"username": user.username, "display_name": user.display_name or user.username})


@bp.route("/auth/logout", methods=["POST"])
def logout():
    row = getattr(g, "api_token", None)
    if row is not None:
        tokens.revoke(row)
    return ok()


@bp.route("/me")
def me():
    s = Settings.get()
    return ok(user={"username": current_user.username,
                    "display_name": current_user.display_name or current_user.username},
              settings=ser.settings(s))


# ── dashboard & offline sync ─────────────────────────────────────────────────

def _dashboard_payload(settings):
    d = stats.dashboard(settings)
    accounts = d["accounts"]
    return {
        "today": today_rome().isoformat(),
        "active_students": d["active_students"],
        "active_groups": d["active_groups"],
        "month_received_cents": d["month_received"],
        "outstanding_cents": d["outstanding_cents"],
        "owed_lessons": d["owed_lessons"],
        "overdue_count": d["overdue_count"],
        "todays": [ser.agenda_item(i) for i in d["todays"]],
        "upcoming": [ser.agenda_item(i) for i in d["upcoming"]],
        "upcoming_count": d["upcoming_count"],
        "to_collect": [ser.student_brief(s, accounts[s.id]) for s in d["to_collect"]],
        "to_collect_count": d["to_collect_count"],
        "low_balance": [ser.student_brief(s, accounts[s.id]) for s in d["low_balance"]],
        "revenue": [{"month": m.isoformat(), "cents": c} for m, c in d["revenue"]],
        "attendance": d["attendance"],
    }


@bp.route("/dashboard")
def dashboard():
    return ok(dashboard=_dashboard_payload(Settings.get()))


@bp.route("/sync")
def sync():
    """Everything the app keeps in its local copy, in one call."""
    settings = Settings.get()
    today = today_rome()
    students = (
        Student.query.options(selectinload(Student.memberships).joinedload(GroupMember.group))
        .order_by(func.lower(Student.name)).all()
    )
    accounts = billing.accounts_for(students, settings=settings)
    groups = Group.query.order_by(func.lower(Group.name)).all()
    start, end = today - timedelta(days=7), today + timedelta(days=14)
    return ok(
        synced_at=now_rome().isoformat(),
        settings=ser.settings(settings),
        packages=[ser.package(p) for p in Package.query.order_by(Package.lessons).all()],
        students=[ser.student_brief(s, accounts[s.id]) for s in students],
        groups=[_group_brief(gr) for gr in groups],
        agenda={"start": start.isoformat(), "end": end.isoformat(),
                "items": [ser.agenda_item(i) for i in schedule.agenda(start, end)]},
        dashboard=_dashboard_payload(settings),
    )


# ── students ─────────────────────────────────────────────────────────────────

@bp.route("/students")
def students_index():
    settings = Settings.get()
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status", "active")
    query = Student.query.options(selectinload(Student.memberships).joinedload(GroupMember.group))
    if status in ("active", "paused", "archived"):
        query = query.filter(Student.status == status)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(func.lower(Student.name).like(like),
                                 func.lower(func.coalesce(Student.guardian_name, "")).like(like),
                                 func.coalesce(Student.phone, "").like(f"%{q}%"),
                                 func.coalesce(Student.guardian_phone, "").like(f"%{q}%")))
    group_id = request.args.get("group", type=int)
    if group_id:
        query = query.join(GroupMember, GroupMember.student_id == Student.id).filter(
            GroupMember.group_id == group_id, GroupMember.left_on.is_(None))
    students = query.order_by(func.lower(Student.name)).all()
    accounts = billing.accounts_for(students, settings=settings)
    balance = request.args.get("balance", "all")
    filters = {
        "owing": lambda a: a.owed_lessons > 0 or a.outstanding_cents > 0,
        "overdue": lambda a: a.overdue,
        "low": lambda a: a.low_balance,
        "settled": lambda a: a.settled,
    }
    if balance in filters:
        students = [s for s in students if filters[balance](accounts[s.id])]
    return ok(students=[ser.student_brief(s, accounts[s.id]) for s in students])


def _fill_student(student, data, creating):
    if creating or "name" in data:
        name = text(data, "name", 120)
        if not name:
            raise ApiError("name_required")
        dup = Student.query.filter(func.lower(Student.name) == name.lower(), Student.id != (student.id or 0)).first()
        if dup:
            raise ApiError("name_taken", 409)
        student.name = name
    for key, limit in (("phone", 40), ("guardian_name", 120), ("guardian_phone", 40), ("school_grade", 60),
                       ("school", 120), ("notes", 5000)):
        if key in data:
            setattr(student, key, text(data, key, limit))
    if "status" in data:
        if data["status"] not in ("active", "paused", "archived"):
            raise ApiError("invalid_status")
        student.status = data["status"]
    if "billing_mode" in data:
        if data["billing_mode"] not in ("postpaid", "prepaid"):
            raise ApiError("invalid_billing_mode")
        student.billing_mode = data["billing_mode"]
    if "enrollment_date" in data:
        student.enrollment_date = parse_date(data.get("enrollment_date") or "") or student.enrollment_date
    if "price_per_lesson_cents" in data:
        student.price_per_lesson_cents = integer(data, "price_per_lesson_cents", None, 0, 100_000_00)
    if "message_language" in data:
        lang = data.get("message_language")
        student.message_language = lang if lang in ("ar", "it") else None


@bp.route("/students", methods=["POST"])
def students_create():
    data = body()
    student = Student(status="active", billing_mode="postpaid", enrollment_date=today_rome(),
                      weekly_plan=1, lesson_days="", last_reset_date=today_rome())
    _fill_student(student, data, creating=True)
    db.session.add(student)
    db.session.flush()
    for gid in data.get("group_ids") or []:
        if isinstance(gid, int) and db.session.get(Group, gid):
            db.session.add(GroupMember(group_id=gid, student_id=student.id, joined_on=student.enrollment_date))
    audit.log("student.create", "student", student.id, student_id=student.id, summary=student.name)
    db.session.commit()
    return _student_detail(student), 201


def _student_detail(student):
    settings = Settings.get()
    acc = billing.account(student, settings=settings)
    today = today_rome()
    group_ids = {gr.id for gr in student.active_groups()}
    upcoming = [i for i in schedule.agenda(today, today + timedelta(days=21), include_cancelled=False)
                if i.student_id == student.id or (i.group_id and i.group_id in group_ids)][:6]
    history = (db.session.query(Attendance, Lesson).join(Lesson, Lesson.id == Attendance.lesson_id)
               .filter(Attendance.student_id == student.id)
               .order_by(Lesson.date.desc(), Lesson.time.desc()).all())
    wa = {}
    number = whatsapp.recipient(student, settings)
    if number:
        wa["payment"] = whatsapp.link(number, whatsapp.message("payment", student, settings, account=acc))
        wa["balance"] = whatsapp.link(number, whatsapp.message("balance", student, settings, account=acc))
        if upcoming:
            wa["upcoming"] = whatsapp.link(number, whatsapp.message("upcoming", student, settings, lesson=upcoming[0]))
        last_hw = next((l.homework for _, l in history if l.homework), None)
        if last_hw:
            wa["homework"] = whatsapp.link(number, whatsapp.message("homework", student, settings, homework=last_hw))
        absent = next((l for a, l in history if a.status in ("absent", "excused")), None)
        if absent is not None:
            wa["absence"] = whatsapp.link(number, whatsapp.message("absence", student, settings, lesson=absent))
    return ok(
        student=ser.student_detail(student, acc, billing.price_for(student, settings)),
        attendance_stats=attendance_svc.attendance_stats(student_id=student.id),
        slots=[ser.slot(sl) for sl in ScheduleSlot.query.filter_by(student_id=student.id, active=True)
               .order_by(ScheduleSlot.weekday)],
        upcoming=[ser.agenda_item(i) for i in upcoming],
        whatsapp=wa,
        whatsapp_number=number,
    )


@bp.route("/students/<int:student_id>")
def students_show(student_id):
    return _student_detail(get_or_404(Student, student_id))


@bp.route("/students/<int:student_id>", methods=["PATCH"])
def students_update(student_id):
    student = get_or_404(Student, student_id)
    _fill_student(student, body(), creating=False)
    audit.log("student.update", "student", student.id, student_id=student.id, summary=student.name)
    db.session.commit()
    return _student_detail(student)


@bp.route("/students/<int:student_id>/lessons")
def students_lessons(student_id):
    student = get_or_404(Student, student_id)
    consumed = {e.attendance_id for e in CreditEntry.query.filter_by(
        student_id=student.id, kind="consume", reversed_at=None)}
    rows = (db.session.query(Attendance, Lesson).join(Lesson, Lesson.id == Attendance.lesson_id)
            .filter(Attendance.student_id == student.id)
            .order_by(Lesson.date.desc(), Lesson.time.desc()).limit(200).all())
    return ok(history=[{
        "lesson": {**ser.lesson_brief(l), "topics": l.topics, "homework": l.homework},
        "attendance": ser.attendance(a, a.id in consumed),
    } for a, l in rows])


@bp.route("/students/<int:student_id>/ledger")
def students_ledger(student_id):
    student = get_or_404(Student, student_id)
    acc = billing.account(student)
    return ok(account=ser.account(acc), items=[ser.ledger_item(i) for i in billing.ledger(student)])


@bp.route("/students/<int:student_id>/assessments")
def students_assessments(student_id):
    student = get_or_404(Student, student_id)
    rows = Assessment.query.filter_by(student_id=student.id).order_by(Assessment.taken_on).all()
    return ok(assessments=[ser.assessment(a) for a in rows])


@bp.route("/students/<int:student_id>/assessments", methods=["POST"])
def students_add_assessment(student_id):
    student = get_or_404(Student, student_id)
    data = body()
    title = text(data, "title", 200)
    kind = data.get("kind") if data.get("kind") in ASSESSMENT_KINDS else "test"
    try:
        score = Decimal(str(data.get("score")))
        max_score = Decimal(str(data.get("max_score", 10)))
    except (InvalidOperation, TypeError):
        raise ApiError("invalid_assessment")
    if not title or max_score <= 0 or not (0 <= score <= max_score):
        raise ApiError("invalid_assessment")
    a = Assessment(student_id=student.id, title=title, kind=kind, score=score, max_score=max_score,
                   taken_on=parse_date(data.get("taken_on") or "") or today_rome(), note=text(data, "note", 500))
    db.session.add(a)
    db.session.flush()
    audit.log("assessment.create", "assessment", a.id, student_id=student.id, summary=f"{title} {score}/{max_score}")
    db.session.commit()
    return ok(assessment=ser.assessment(a)), 201


@bp.route("/assessments/<int:assessment_id>", methods=["DELETE"])
def assessments_delete(assessment_id):
    a = get_or_404(Assessment, assessment_id)
    audit.log("assessment.delete", "assessment", a.id, student_id=a.student_id, summary=a.title)
    db.session.delete(a)
    db.session.commit()
    return ok()


# ── groups & weekly slots ────────────────────────────────────────────────────

def _group_brief(gr):
    return {**ser.group_ref(gr), "subject": gr.subject, "level": gr.level, "active": gr.active,
            "members_count": len(gr.members()), "default_duration_min": gr.default_duration_min,
            "slots": [ser.slot(sl) for sl in ScheduleSlot.query.filter_by(group_id=gr.id, active=True)
                      .order_by(ScheduleSlot.weekday)]}


@bp.route("/groups")
def groups_index():
    groups = Group.query.order_by(Group.active.desc(), func.lower(Group.name)).all()
    return ok(groups=[_group_brief(gr) for gr in groups])


@bp.route("/groups/<int:group_id>")
def groups_show(group_id):
    gr = get_or_404(Group, group_id)
    members = gr.members()
    accounts = billing.accounts_for(members) if members else {}
    current = {m.student_id: m.id for m in gr.memberships if m.left_on is None}
    lessons = Lesson.query.filter_by(group_id=gr.id).order_by(Lesson.date.desc(), Lesson.time.desc()).limit(30).all()
    return ok(group={**_group_brief(gr), "notes": gr.notes},
              members=[{**ser.student_brief(s, accounts[s.id]), "membership_id": current.get(s.id)} for s in members],
              lessons=[ser.lesson_brief(l) for l in lessons])


def _fill_group(group, data, creating):
    if creating or "name" in data:
        name = text(data, "name", 120)
        if not name:
            raise ApiError("name_required")
        dup = Group.query.filter(func.lower(Group.name) == name.lower(), Group.id != (group.id or 0)).first()
        if dup:
            raise ApiError("name_taken", 409)
        group.name = name
    for key, limit in (("subject", 120), ("level", 60), ("notes", 5000)):
        if key in data:
            setattr(group, key, text(data, key, limit))
    if "color" in data and re.fullmatch(r"#[0-9a-fA-F]{6}", str(data.get("color") or "")):
        group.color = data["color"]
    if "default_duration_min" in data:
        group.default_duration_min = integer(data, "default_duration_min", 60, 15, 600)
    if "active" in data:
        group.active = bool(data["active"])


@bp.route("/groups", methods=["POST"])
def groups_create():
    group = Group(color=COLORS[Group.query.count() % len(COLORS)],
                  default_duration_min=Settings.get().default_duration_min)
    _fill_group(group, body(), creating=True)
    db.session.add(group)
    db.session.flush()
    audit.log("group.create", "group", group.id, summary=group.name)
    db.session.commit()
    return groups_show(group.id), 201


@bp.route("/groups/<int:group_id>", methods=["PATCH"])
def groups_update(group_id):
    group = get_or_404(Group, group_id)
    _fill_group(group, body(), creating=False)
    audit.log("group.update", "group", group.id, summary=group.name)
    db.session.commit()
    return groups_show(group.id)


@bp.route("/groups/members/<int:member_id>", methods=["DELETE"])
def groups_remove_member(member_id):
    m = get_or_404(GroupMember, member_id)
    if m.left_on is None:
        m.left_on = today_rome()
        audit.log("group.leave", "group", m.group_id, student_id=m.student_id, summary=m.group.name)
        if m.joined_on >= m.left_on:  # joined today by mistake: keep history consistent
            db.session.delete(m)
        db.session.commit()
    return ok()


@bp.route("/groups/<int:group_id>/members", methods=["POST"])
def groups_add_members(group_id):
    gr = get_or_404(Group, group_id)
    current = {m.student_id for m in gr.memberships if m.left_on is None}
    added = 0
    for sid in body().get("student_ids") or []:
        if isinstance(sid, int) and sid not in current and db.session.get(Student, sid):
            db.session.add(GroupMember(group_id=gr.id, student_id=sid, joined_on=today_rome()))
            audit.log("group.join", "group", gr.id, student_id=sid, summary=gr.name)
            added += 1
    db.session.commit()
    return ok(added=added)


@bp.route("/slots", methods=["POST"])
def slots_create():
    data = body()
    weekday = integer(data, "weekday", None, 0, 6)
    start = valid_time(str(data.get("start_time") or ""))
    duration = integer(data, "duration_min", Settings.get().default_duration_min, 15, 600)
    group = db.session.get(Group, integer(data, "group_id") or 0)
    student = None if group else db.session.get(Student, integer(data, "student_id") or 0)
    if weekday is None or not start or (group is None and student is None):
        raise ApiError("invalid_slot")
    conflicts = schedule.slot_conflicts(weekday, start, duration)
    sl = ScheduleSlot(group_id=group.id if group else None, student_id=student.id if student else None,
                      weekday=weekday, start_time=start, duration_min=duration,
                      valid_from=parse_date(data.get("valid_from") or "") or today_rome())
    db.session.add(sl)
    db.session.flush()
    audit.log("slot.create", "slot", sl.id, student_id=sl.student_id, summary=f"{sl.title} · {start}")
    db.session.commit()
    return ok(slot=ser.slot(sl), conflicts=[ser.slot(c) for c in conflicts]), 201


@bp.route("/slots/<int:slot_id>", methods=["DELETE"])
def slots_end(slot_id):
    sl = get_or_404(ScheduleSlot, slot_id)
    sl.active = False
    sl.valid_until = today_rome()
    audit.log("slot.end", "slot", sl.id, student_id=sl.student_id, summary=f"{sl.title} · {sl.start_time}")
    db.session.commit()
    return ok()


# ── agenda & lessons ─────────────────────────────────────────────────────────

@bp.route("/agenda")
def agenda():
    start = parse_date(request.args.get("start") or "") or week_start(today_rome())
    end = parse_date(request.args.get("end") or "") or start + timedelta(days=6)
    if end < start or (end - start).days > 62:
        raise ApiError("invalid_range")
    return ok(start=start.isoformat(), end=end.isoformat(),
              items=[ser.agenda_item(i) for i in schedule.agenda(start, end)])


def _conflict_payload(items):
    return [{"time": i.time, "end_time": i.end_time,
             "title": i.title if i.is_virtual else i.display_title} for i in items]


@bp.route("/lessons", methods=["POST"])
def lessons_create():
    data = body()
    on = parse_date(str(data.get("date") or ""))
    start = valid_time(str(data.get("time") or ""))
    duration = integer(data, "duration_min", Settings.get().default_duration_min, 15, 600)
    kind = data.get("kind") if data.get("kind") in LESSON_KINDS else "regular"
    group = db.session.get(Group, integer(data, "group_id") or 0)
    student = None if group else db.session.get(Student, integer(data, "student_id") or 0)
    if not on or not start:
        raise ApiError("invalid_datetime")
    if group is None and student is None:
        raise ApiError("choose_target")
    conflicts = schedule.conflicts(on, start, duration)
    if conflicts and not data.get("confirm_conflict"):
        raise ApiError("conflict", 409, conflicts=_conflict_payload(conflicts))
    lesson = Lesson(group_id=group.id if group else None, student_id=student.id if student else None,
                    date=on, time=start, duration_min=duration, kind=kind, status="scheduled",
                    title=text(data, "title", 200))
    db.session.add(lesson)
    db.session.flush()
    audit.log("lesson.create", "lesson", lesson.id, student_id=lesson.student_id,
              summary=f"{lesson.display_title} {on} {start}")
    db.session.commit()
    return _lesson_detail(lesson), 201


@bp.route("/lessons/open-slot", methods=["POST"])
def lessons_open_slot():
    data = body()
    sl = get_or_404(ScheduleSlot, integer(data, "slot_id") or 0)
    on = parse_date(str(data.get("date") or ""))
    if on is None:
        raise ApiError("invalid_datetime")
    lesson, _ = schedule.materialize(sl, on, user_id=uid())
    return _lesson_detail(lesson)


@bp.route("/lessons/quick/<int:student_id>", methods=["POST"])
def lessons_quick(student_id):
    student = get_or_404(Student, student_id)
    now = now_rome()
    lesson = Lesson(student_id=student.id, date=now.date(), time=now.strftime("%H:%M"),
                    duration_min=Settings.get().default_duration_min, status="scheduled", kind="regular")
    db.session.add(lesson)
    db.session.flush()
    audit.log("lesson.create", "lesson", lesson.id, student_id=student.id,
              summary=f"{student.name} · {lesson.date} {lesson.time}")
    attendance_svc.mark(lesson, [{"student_id": student.id, "status": "present"}], user_id=uid())
    return _lesson_detail(lesson), 201


def _lesson_detail(lesson):
    settings = Settings.get()
    participants = lesson.participants()
    accounts = billing.accounts_for(participants, settings=settings) if participants else {}
    consumed = {a.id for a in lesson.attendance if billing.active_consumption(a) is not None}
    previous = None
    if lesson.group_id or lesson.student_id:
        q = Lesson.query.filter(Lesson.id != lesson.id, Lesson.status != "cancelled",
                                (Lesson.date < lesson.date) | ((Lesson.date == lesson.date) & (Lesson.time < lesson.time)))
        q = q.filter(Lesson.group_id == lesson.group_id) if lesson.group_id else q.filter(
            Lesson.student_id == lesson.student_id)
        previous = q.order_by(Lesson.date.desc(), Lesson.time.desc()).first()
    members = {s.id for s in lesson.group.members(on=lesson.date)} if lesson.group_id else set()
    people = []
    for s in participants:
        a = lesson.attendance_for(s.id)
        number = whatsapp.recipient(s, settings)
        wa = None
        if number and a is not None and a.status in ("absent", "excused"):
            wa = {"kind": "absence", "url": whatsapp.link(number, whatsapp.message("absence", s, settings, lesson=lesson))}
        elif number and lesson.homework:
            wa = {"kind": "homework",
                  "url": whatsapp.link(number, whatsapp.message("homework", s, settings, homework=lesson.homework))}
        people.append({
            "student": ser.student_brief(s, accounts.get(s.id)),
            "attendance": ser.attendance(a, a is not None and a.id in consumed),
            "guest": bool(lesson.group_id) and s.id not in members,
            "whatsapp": wa,
        })
    return ok(lesson={
        **ser.lesson_brief(lesson),
        "topics": lesson.topics, "notes": lesson.notes, "homework": lesson.homework,
        "observations": lesson.observations, "version": lesson.version,
        "cancel_reason": lesson.cancel_reason, "rescheduled_from": lesson.rescheduled_from,
        "slot_id": lesson.slot_id, "slot_date": lesson.slot_date.isoformat() if lesson.slot_date else None,
    }, participants=people,
        previous_homework={"date": previous.date.isoformat(), "text": previous.homework}
        if previous is not None and previous.homework else None)


@bp.route("/lessons/<int:lesson_id>")
def lessons_show(lesson_id):
    return _lesson_detail(get_or_404(Lesson, lesson_id))


@bp.route("/lessons/<int:lesson_id>/notebook", methods=["PATCH"])
def lessons_notebook(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    data = body()
    version = integer(data, "version")
    if version is None:
        raise ApiError("version_required")
    if version != lesson.version and not data.get("force"):
        raise ApiError("version_conflict", 409, version=lesson.version,
                       server={f: getattr(lesson, f) for f in ("title", "topics", "notes", "homework", "observations")})
    changed = False
    for field, limit in (("title", 200), ("topics", 20000), ("notes", 20000), ("homework", 20000),
                         ("observations", 20000)):
        if field in data:
            value = text(data, field, limit)
            if getattr(lesson, field) != value:
                setattr(lesson, field, value)
                changed = True
    if "kind" in data and data["kind"] in LESSON_KINDS:
        lesson.kind = data["kind"]
        changed = True
    if changed:
        lesson.version += 1
        db.session.commit()
    return ok(version=lesson.version)


@bp.route("/lessons/<int:lesson_id>/attendance", methods=["PUT"])
def lessons_attendance(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    entries = body().get("entries")
    if not isinstance(entries, list) or not entries:
        raise ApiError("invalid_entries")
    clean = []
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("student_id"), int):
            raise ApiError("invalid_entries")
        if db.session.get(Student, e["student_id"]) is None:
            raise ApiError("invalid_entries")
        clean.append(e)
    try:
        attendance_svc.mark(lesson, clean, user_id=uid())
    except AttendanceError as err:
        raise ApiError(err.code, 422)
    return _lesson_detail(lesson)


@bp.route("/lessons/<int:lesson_id>/cancel", methods=["POST"])
def lessons_cancel(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    attendance_svc.cancel_lesson(lesson, text(body(), "reason", 255, ""), user_id=uid())
    return _lesson_detail(lesson)


@bp.route("/lessons/<int:lesson_id>/restore", methods=["POST"])
def lessons_restore(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    attendance_svc.restore_lesson(lesson, user_id=uid())
    return _lesson_detail(lesson)


@bp.route("/lessons/<int:lesson_id>/free", methods=["POST"])
def lessons_free(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    attendance_svc.set_free(lesson, bool(body().get("is_free")), user_id=uid())
    return _lesson_detail(lesson)


@bp.route("/lessons/<int:lesson_id>/reschedule", methods=["POST"])
def lessons_reschedule(lesson_id):
    lesson = get_or_404(Lesson, lesson_id)
    data = body()
    on = parse_date(str(data.get("date") or ""))
    start = valid_time(str(data.get("time") or ""))
    duration = integer(data, "duration_min", lesson.duration_min, 15, 600)
    if not on or not start:
        raise ApiError("invalid_datetime")
    conflicts = schedule.conflicts(on, start, duration, exclude_lesson_id=lesson.id)
    if conflicts and not data.get("confirm_conflict"):
        raise ApiError("conflict", 409, conflicts=_conflict_payload(conflicts))
    if not lesson.rescheduled_from:
        lesson.rescheduled_from = f"{lesson.date.isoformat()} {lesson.time}"
    old = f"{lesson.date} {lesson.time}"
    lesson.date, lesson.time, lesson.duration_min = on, start, duration
    audit.log("lesson.reschedule", "lesson", lesson.id, student_id=lesson.student_id,
              summary=f"{lesson.display_title} · {old} → {on} {start}")
    db.session.commit()
    return _lesson_detail(lesson)


# ── payments ─────────────────────────────────────────────────────────────────

@bp.route("/payments")
def payments_index():
    today = today_rome()
    first = parse_date(request.args.get("from") or "") or month_start(today)
    last = parse_date(request.args.get("to") or "") or today
    pq = Payment.query.filter(Payment.paid_on >= first, Payment.paid_on <= last)
    cq = Charge.query.filter(Charge.charged_on >= first, Charge.charged_on <= last)
    sid = request.args.get("student", type=int)
    if sid:
        pq, cq = pq.filter(Payment.student_id == sid), cq.filter(Charge.student_id == sid)
    payments = pq.order_by(Payment.paid_on.desc(), Payment.id.desc()).all()
    charges = cq.order_by(Charge.charged_on.desc(), Charge.id.desc()).all()
    received = sum(p.amount_cents for p in payments if p.voided_at is None)
    return ok(**{"from": first.isoformat(), "to": last.isoformat()}, received_cents=received,
              payments=[ser.payment(p) for p in payments], charges=[ser.charge(c) for c in charges])


@bp.route("/packages")
def packages_index():
    return ok(packages=[ser.package(p) for p in Package.query.filter_by(active=True).order_by(Package.lessons)])


@bp.route("/students/<int:student_id>/collect", methods=["POST"])
def payments_collect(student_id):
    """Sell lessons and/or record money received, atomically and idempotently."""
    student = get_or_404(Student, student_id)
    data = body()
    mode = data.get("mode") or "lessons"
    package = None
    lessons = 0
    unit_price = None
    if mode == "package":
        package = db.session.get(Package, integer(data, "package_id") or 0)
        if package is None:
            raise ApiError("choose_package")
    elif mode == "lessons":
        lessons = integer(data, "lessons", 0, 0, 500)
        unit_price = integer(data, "unit_price_cents", None, 0, 100_000_00)
    elif mode != "payment_only":
        raise ApiError("invalid_mode")
    method = data.get("method") or "cash"
    if method not in PAYMENT_METHODS:
        raise ApiError("invalid_method")
    try:
        charge, payment, created = billing.collect(
            student, idempotency_key=text(data, "idempotency_key", 64, ""), lessons=lessons,
            unit_price_cents=unit_price, package=package, description=text(data, "description", 200),
            paid_cents=integer(data, "amount_paid_cents", 0, 0, 100_000_00), method=method,
            paid_on=parse_date(str(data.get("paid_on") or "")) or today_rome(),
            note=text(data, "note", 255), user_id=uid())
    except BillingError as err:
        raise ApiError(err.code, 422)
    return ok(created=created, charge=ser.charge(charge) if charge else None,
              payment=ser.payment(payment) if payment else None,
              account=ser.account(billing.account(student))), (201 if created else 200)


@bp.route("/payments/<int:payment_id>/void", methods=["POST"])
def payments_void(payment_id):
    p = get_or_404(Payment, payment_id)
    try:
        billing.void_payment(p, text(body(), "reason", 255, ""), user_id=uid())
    except BillingError as err:
        raise ApiError(err.code, 422)
    return ok(payment=ser.payment(p))


@bp.route("/charges/<int:charge_id>/void", methods=["POST"])
def charges_void(charge_id):
    c = get_or_404(Charge, charge_id)
    try:
        billing.void_charge(c, text(body(), "reason", 255, ""), user_id=uid())
    except BillingError as err:
        raise ApiError(err.code, 422)
    return ok(charge=ser.charge(c))


# ── PDFs (same generators as the website) ────────────────────────────────────

def _pdf(view, *args):
    from . import reports

    return getattr(reports, view)(*args)


@bp.route("/pdf/receipt/<int:payment_id>")
def pdf_receipt(payment_id):
    return _pdf("receipt", payment_id)


@bp.route("/pdf/students/<int:student_id>/report")
def pdf_student_report(student_id):
    return _pdf("student_report", student_id)


@bp.route("/pdf/students/<int:student_id>/statement")
def pdf_statement(student_id):
    return _pdf("statement", student_id)


@bp.route("/pdf/lessons/<int:lesson_id>")
def pdf_lesson(lesson_id):
    return _pdf("lesson_summary", lesson_id)


@bp.route("/pdf/groups/<int:group_id>")
def pdf_group(group_id):
    return _pdf("group_report", group_id)


@bp.route("/pdf/attendance")
def pdf_attendance():
    return _pdf("attendance_sheet")


@bp.route("/pdf/finance")
def pdf_finance():
    return _pdf("finance")
