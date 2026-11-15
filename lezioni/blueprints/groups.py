import re

from flask import Blueprint, redirect, render_template, request, url_for
from sqlalchemy import func

from ..extensions import db
from ..models import Group, GroupMember, Lesson, ScheduleSlot, Settings, Student
from ..services import audit, billing
from ..utils import today_rome
from ._helpers import back, flash_error, flash_ok, form_int, form_str, get_or_404

bp = Blueprint("groups", __name__, url_prefix="/groups")

COLORS = ["#2f6f4f", "#2563eb", "#b45309", "#7c3aed", "#0e7490", "#be185d", "#4d7c0f", "#b91c1c"]


@bp.route("/")
def index():
    show = request.args.get("show", "active")
    q = Group.query
    if show != "all":
        q = q.filter(Group.active.is_(show == "active"))
    groups = q.order_by(func.lower(Group.name)).all()
    slots = {}
    for s in ScheduleSlot.query.filter(ScheduleSlot.group_id.isnot(None), ScheduleSlot.active.is_(True)):
        slots.setdefault(s.group_id, []).append(s)
    return render_template("groups/index.html", groups=groups, slots=slots, show=show, nav="groups")


def _fill(group):
    name = form_str("name", 120)
    if not name:
        return "err_name_required"
    dup = Group.query.filter(func.lower(Group.name) == name.lower(), Group.id != (group.id or 0)).first()
    if dup:
        return "err_name_taken"
    group.name = name
    group.subject = form_str("subject", 120) or None
    group.level = form_str("level", 60) or None
    color = form_str("color", 7)
    group.color = color if re.fullmatch(r"#[0-9a-fA-F]{6}", color or "") else (group.color or COLORS[0])
    group.default_duration_min = form_int("default_duration_min", 60, 15, 600)
    group.notes = form_str("notes", 5000) or None
    return None


@bp.route("/new", methods=["GET", "POST"])
def new():
    group = Group(color=COLORS[Group.query.count() % len(COLORS)], default_duration_min=Settings.get().default_duration_min)
    if request.method == "POST":
        err = _fill(group)
        if err:
            flash_error(err)
            return render_template("groups/form.html", group=group, colors=COLORS, nav="groups"), 400
        db.session.add(group)
        db.session.flush()
        audit.log("group.create", "group", group.id, summary=group.name)
        db.session.commit()
        flash_ok("msg_saved")
        return redirect(url_for("groups.show", group_id=group.id))
    return render_template("groups/form.html", group=group, colors=COLORS, nav="groups")


@bp.route("/<int:group_id>/edit", methods=["GET", "POST"])
def edit(group_id):
    group = get_or_404(Group, group_id)
    if request.method == "POST":
        err = _fill(group)
        if err:
            db.session.rollback()
            flash_error(err)
            return redirect(url_for("groups.edit", group_id=group.id))
        audit.log("group.update", "group", group.id, summary=group.name)
        db.session.commit()
        flash_ok("msg_saved")
        return redirect(url_for("groups.show", group_id=group.id))
    return render_template("groups/form.html", group=group, colors=COLORS, nav="groups")


@bp.route("/<int:group_id>")
def show(group_id):
    settings = Settings.get()
    group = get_or_404(Group, group_id)
    members = group.members()
    accounts = billing.accounts_for(members, settings=settings)
    member_ids = {s.id for s in members}
    candidates = (
        Student.query.filter(Student.status != "archived").order_by(func.lower(Student.name)).all()
    )
    candidates = [s for s in candidates if s.id not in member_ids]
    slots = ScheduleSlot.query.filter_by(group_id=group.id, active=True).order_by(ScheduleSlot.weekday).all()
    lessons = (
        Lesson.query.filter_by(group_id=group.id).order_by(Lesson.date.desc(), Lesson.time.desc()).limit(30).all()
    )
    current = {m.student_id: m for m in group.memberships if m.left_on is None}
    return render_template(
        "groups/show.html", group=group, members=members, accounts=accounts, candidates=candidates,
        slots=slots, lessons=lessons, current=current, nav="groups",
    )


@bp.route("/<int:group_id>/members", methods=["POST"])
def add_members(group_id):
    group = get_or_404(Group, group_id)
    added = 0
    current = {m.student_id for m in group.memberships if m.left_on is None}
    for sid in request.form.getlist("student_ids"):
        try:
            sid = int(sid)
        except ValueError:
            continue
        if sid in current or db.session.get(Student, sid) is None:
            continue
        db.session.add(GroupMember(group_id=group.id, student_id=sid, joined_on=today_rome()))
        audit.log("group.join", "group", group.id, student_id=sid, summary=group.name)
        added += 1
    db.session.commit()
    if added:
        flash_ok("msg_members_added", n=added)
    return redirect(url_for("groups.show", group_id=group.id))


@bp.route("/members/<int:member_id>/remove", methods=["POST"])
def remove_member(member_id):
    m = get_or_404(GroupMember, member_id)
    if m.left_on is None:
        m.left_on = today_rome()
        if m.joined_on >= m.left_on:  # joined today by mistake: keep history consistent
            db.session.delete(m)
        audit.log("group.leave", "group", m.group_id, student_id=m.student_id, summary=m.group.name)
        db.session.commit()
        flash_ok("msg_saved")
    return back("groups.show", group_id=m.group_id)


@bp.route("/<int:group_id>/active", methods=["POST"])
def toggle_active(group_id):
    group = get_or_404(Group, group_id)
    group.active = not group.active
    audit.log("group.active" if group.active else "group.archive", "group", group.id, summary=group.name)
    db.session.commit()
    flash_ok("msg_saved")
    return redirect(url_for("groups.show", group_id=group.id))
