from datetime import timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, redirect, render_template, request, url_for
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    ASSESSMENT_KINDS, Assessment, Attendance, Charge, CreditEntry, Group, GroupMember, Lesson, Payment,
    ScheduleSlot, Settings, Student,
)
from ..services import attendance as attendance_svc
from ..services import audit, billing, schedule, whatsapp
from ..utils import parse_date, parse_money, today_rome
from ._helpers import back, flash_error, flash_ok, form_int, form_str, get_or_404

bp = Blueprint("students", __name__, url_prefix="/students")

SORTS = ("name", "balance", "recent", "enrolled")


@bp.route("/")
def index():
    settings = Settings.get()
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status", "active")
    group_id = request.args.get("group", type=int)
    balance = request.args.get("balance", "all")
    sort = request.args.get("sort", "name")

    query = Student.query.options(selectinload(Student.memberships).joinedload(GroupMember.group))
    if status in ("active", "paused", "archived"):
        query = query.filter(Student.status == status)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(
            func.lower(Student.name).like(like),
            func.lower(func.coalesce(Student.guardian_name, "")).like(like),
            func.coalesce(Student.phone, "").like(f"%{q}%"),
            func.coalesce(Student.guardian_phone, "").like(f"%{q}%"),
        ))
    if group_id:
        query = query.join(GroupMember, GroupMember.student_id == Student.id).filter(
            GroupMember.group_id == group_id, GroupMember.left_on.is_(None)
        )
    students = query.order_by(func.lower(Student.name)).all()
    accounts = billing.accounts_for(students, settings=settings)

    if balance == "owing":
        students = [s for s in students if accounts[s.id].owed_lessons > 0 or accounts[s.id].outstanding_cents > 0]
    elif balance == "overdue":
        students = [s for s in students if accounts[s.id].overdue]
    elif balance == "low":
        students = [s for s in students if accounts[s.id].low_balance]
    elif balance == "settled":
        students = [s for s in students if accounts[s.id].settled]

    if sort == "balance":
        students.sort(key=lambda s: (accounts[s.id].remaining, -accounts[s.id].outstanding_cents))
    elif sort == "enrolled":
        students.sort(key=lambda s: s.enrollment_date or today_rome(), reverse=True)
    elif sort == "recent":
        last = dict(
            db.session.query(Attendance.student_id, func.max(Lesson.date))
            .join(Lesson).group_by(Attendance.student_id).all()
        )
        students.sort(key=lambda s: last.get(s.id) or s.enrollment_date or today_rome(), reverse=True)

    groups = Group.query.filter_by(active=True).order_by(func.lower(Group.name)).all()
    return render_template(
        "students/index.html", students=students, accounts=accounts, groups=groups,
        q=q, status=status, group_id=group_id, balance=balance, sort=sort, nav="students",
    )


def _fill_student(student, settings):
    errors = []
    name = form_str("name", 120)
    if not name:
        errors.append("err_name_required")
    else:
        dup = Student.query.filter(func.lower(Student.name) == name.lower(), Student.id != (student.id or 0)).first()
        if dup:
            errors.append("err_name_taken")
    student.name = name
    student.phone = form_str("phone", 40) or None
    student.guardian_name = form_str("guardian_name", 120) or None
    student.guardian_phone = form_str("guardian_phone", 40) or None
    student.school_grade = form_str("school_grade", 60) or None
    student.school = form_str("school", 120) or None
    status = form_str("status") or "active"
    student.status = status if status in ("active", "paused", "archived") else "active"
    billing_mode = form_str("billing_mode") or "postpaid"
    student.billing_mode = billing_mode if billing_mode in ("postpaid", "prepaid") else "postpaid"
    student.enrollment_date = parse_date(form_str("enrollment_date")) or student.enrollment_date or today_rome()
    raw_price = form_str("price")
    if raw_price:
        price = parse_money(raw_price)
        if price is None:
            errors.append("err_invalid_amount")
        student.price_per_lesson_cents = price
    else:
        student.price_per_lesson_cents = None
    lang = form_str("message_language")
    student.message_language = lang if lang in ("ar", "it") else None
    student.notes = form_str("notes", 5000) or None
    return errors


@bp.route("/new", methods=["GET", "POST"])
def new():
    settings = Settings.get()
    student = Student(status="active", billing_mode="postpaid", enrollment_date=today_rome())
    groups = Group.query.filter_by(active=True).order_by(func.lower(Group.name)).all()
    if request.method == "POST":
        errors = _fill_student(student, settings)
        if errors:
            for e in errors:
                flash_error(e)
            return render_template("students/form.html", student=student, groups=groups, selected=[],
                                   nav="students"), 400
        student.weekly_plan = 1
        student.lesson_days = ""
        student.last_reset_date = today_rome()
        db.session.add(student)
        db.session.flush()
        for gid in request.form.getlist("groups"):
            try:
                gid = int(gid)
            except ValueError:
                continue
            if db.session.get(Group, gid):
                db.session.add(GroupMember(group_id=gid, student_id=student.id, joined_on=student.enrollment_date))
        audit.log("student.create", "student", student.id, student_id=student.id, summary=student.name)
        db.session.commit()
        flash_ok("msg_student_created", name=student.name)
        return redirect(url_for("students.show", student_id=student.id))
    return render_template("students/form.html", student=student, groups=groups, selected=[], nav="students")


@bp.route("/<int:student_id>/edit", methods=["GET", "POST"])
def edit(student_id):
    settings = Settings.get()
    student = get_or_404(Student, student_id)
    if request.method == "POST":
        errors = _fill_student(student, settings)
        if errors:
            db.session.rollback()
            for e in errors:
                flash_error(e)
            return redirect(url_for("students.edit", student_id=student_id))
        audit.log("student.update", "student", student.id, student_id=student.id, summary=student.name)
        db.session.commit()
        flash_ok("msg_saved")
        return redirect(url_for("students.show", student_id=student.id))
    return render_template("students/form.html", student=student, groups=[], selected=[], nav="students")


@bp.route("/<int:student_id>")
def show(student_id):
    settings = Settings.get()
    student = get_or_404(Student, student_id)
    tab = request.args.get("tab", "overview")
    acc = billing.account(student, settings=settings)
    today = today_rome()

    history = (
        db.session.query(Attendance, Lesson)
        .join(Lesson, Lesson.id == Attendance.lesson_id)
        .filter(Attendance.student_id == student.id)
        .order_by(Lesson.date.desc(), Lesson.time.desc())
        .all()
    )
    consumed_ids = {
        e.attendance_id for e in CreditEntry.query.filter_by(student_id=student.id, kind="consume", reversed_at=None)
    }
    att = attendance_svc.attendance_stats(student_id=student.id)
    upcoming = [
        i for i in schedule.agenda(today, today + timedelta(days=21), include_cancelled=False)
        if (i.student_id == student.id) or (i.group_id and i.group_id in {g.id for g in student.active_groups()})
    ][:6]
    if not upcoming:
        upcoming = []
    assessments = Assessment.query.filter_by(student_id=student.id).order_by(Assessment.taken_on).all()
    slots = ScheduleSlot.query.filter_by(student_id=student.id, active=True).order_by(ScheduleSlot.weekday).all()
    memberships = GroupMember.query.filter_by(student_id=student.id).order_by(GroupMember.joined_on.desc()).all()
    groups = Group.query.filter_by(active=True).order_by(func.lower(Group.name)).all()
    last_hw = next((l.homework for a, l in history if l.homework), None)
    next_lesson = upcoming[0] if upcoming else None

    wa_number = whatsapp.recipient(student, settings)
    wa = {}
    if wa_number:
        wa["payment"] = whatsapp.link(wa_number, whatsapp.message("payment", student, settings, account=acc))
        wa["balance"] = whatsapp.link(wa_number, whatsapp.message("balance", student, settings, account=acc))
        if next_lesson is not None:
            wa["upcoming"] = whatsapp.link(wa_number, whatsapp.message("upcoming", student, settings, lesson=next_lesson))
        if last_hw:
            wa["homework"] = whatsapp.link(wa_number, whatsapp.message("homework", student, settings, homework=last_hw))
        last_absent = next(((a, l) for a, l in history if a.status in ("absent", "excused")), None)
        if last_absent:
            wa["absence"] = whatsapp.link(
                wa_number, whatsapp.message("absence", student, settings, lesson=last_absent[1])
            )

    return render_template(
        "students/show.html", student=student, acc=acc, tab=tab, history=history, consumed_ids=consumed_ids,
        att=att, upcoming=upcoming, assessments=assessments, slots=slots, memberships=memberships,
        groups=groups, ledger=billing.ledger(student), wa=wa, wa_number=wa_number,
        wa_display=(chr(0x200E) + "+" + wa_number) if wa_number else "",
        price=billing.price_for(student, settings), assessment_kinds=ASSESSMENT_KINDS, nav="students",
    )


@bp.route("/<int:student_id>/status", methods=["POST"])
def set_status(student_id):
    student = get_or_404(Student, student_id)
    status = form_str("status")
    if status not in ("active", "paused", "archived"):
        abort(400)
    student.status = status
    if status == "archived":
        for m in student.memberships:
            if m.left_on is None:
                m.left_on = today_rome()
        for s in ScheduleSlot.query.filter_by(student_id=student.id, active=True):
            s.active = False
            s.valid_until = today_rome()
    audit.log("student.status", "student", student.id, student_id=student.id, summary=status)
    db.session.commit()
    flash_ok("msg_saved")
    return back("students.show", student_id=student.id)


@bp.route("/<int:student_id>/delete", methods=["POST"])
def delete(student_id):
    student = get_or_404(Student, student_id)
    has_history = any(
        db.session.query(m.id).filter(m.student_id == student.id).first()
        for m in (Attendance, Charge, Payment, CreditEntry, Lesson)
    )
    if has_history:
        flash_error("err_student_has_history")
        return redirect(url_for("students.show", student_id=student.id))
    name = student.name
    GroupMember.query.filter_by(student_id=student.id).delete()
    ScheduleSlot.query.filter_by(student_id=student.id).delete()
    Assessment.query.filter_by(student_id=student.id).delete()
    audit.log("student.delete", "student", student.id, summary=name)
    db.session.delete(student)
    db.session.commit()
    flash_ok("msg_student_deleted", name=name)
    return redirect(url_for("students.index"))


@bp.route("/<int:student_id>/groups", methods=["POST"])
def join_group(student_id):
    student = get_or_404(Student, student_id)
    group = get_or_404(Group, form_int("group_id", 0))
    if any(m.group_id == group.id and m.left_on is None for m in student.memberships):
        return back("students.show", student_id=student.id)
    db.session.add(GroupMember(group_id=group.id, student_id=student.id, joined_on=today_rome()))
    audit.log("group.join", "group", group.id, student_id=student.id, summary=group.name)
    db.session.commit()
    flash_ok("msg_saved")
    return back("students.show", student_id=student.id)


@bp.route("/<int:student_id>/assessments", methods=["POST"])
def add_assessment(student_id):
    student = get_or_404(Student, student_id)
    title = form_str("title", 200)
    kind = form_str("kind") or "test"
    try:
        score = Decimal(form_str("score").replace(",", "."))
        max_score = Decimal(form_str("max_score").replace(",", ".") or "10")
    except InvalidOperation:
        score = max_score = None
    if not title or score is None or max_score is None or max_score <= 0 or not (0 <= score <= max_score):
        flash_error("err_invalid_assessment")
        return redirect(url_for("students.show", student_id=student.id, tab="grades"))
    a = Assessment(
        student_id=student.id, title=title, kind=kind if kind in ASSESSMENT_KINDS else "test",
        taken_on=parse_date(form_str("taken_on")) or today_rome(), score=score, max_score=max_score,
        note=form_str("note", 500) or None,
    )
    db.session.add(a)
    db.session.flush()
    audit.log("assessment.create", "assessment", a.id, student_id=student.id, summary=f"{title} {score}/{max_score}")
    db.session.commit()
    flash_ok("msg_saved")
    return redirect(url_for("students.show", student_id=student.id, tab="grades"))


@bp.route("/assessments/<int:assessment_id>/delete", methods=["POST"])
def delete_assessment(assessment_id):
    a = get_or_404(Assessment, assessment_id)
    sid = a.student_id
    audit.log("assessment.delete", "assessment", a.id, student_id=sid, summary=a.title)
    db.session.delete(a)
    db.session.commit()
    flash_ok("msg_deleted")
    return redirect(url_for("students.show", student_id=sid, tab="grades"))
