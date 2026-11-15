from datetime import timedelta

from flask import Blueprint, jsonify, request, url_for

from ..extensions import db
from ..models import Lesson
from ..services import schedule
from ..utils import parse_date, today_rome

bp = Blueprint("api", __name__, url_prefix="/api")

NOTEBOOK_FIELDS = {"title": 200, "topics": 20000, "notes": 20000, "homework": 20000, "observations": 20000}
DEFAULT_COLOR = "#475569"


def _iso(d, t):
    return f"{d.isoformat()}T{t}:00"


@bp.route("/events")
def events():
    start = parse_date((request.args.get("start") or "")[:10]) or today_rome() - timedelta(days=7)
    end = parse_date((request.args.get("end") or "")[:10]) or start + timedelta(days=42)
    if (end - start).days > 120:
        end = start + timedelta(days=120)
    out = []
    for item in schedule.agenda(start, end):
        if item.is_virtual:
            out.append({
                "id": f"slot-{item.slot.id}-{item.date.isoformat()}",
                "title": item.title,
                "start": _iso(item.date, item.time),
                "end": _iso(item.date, item.end_time),
                "color": item.color or DEFAULT_COLOR,
                "extendedProps": {"virtual": True, "slot_id": item.slot.id, "date": item.date.isoformat()},
            })
        else:
            out.append({
                "id": f"lesson-{item.id}",
                "title": item.display_title,
                "start": _iso(item.date, item.time),
                "end": _iso(item.date, item.end_time),
                "color": item.color or DEFAULT_COLOR,
                "url": url_for("lessons.show", lesson_id=item.id),
                "classNames": [f"st-{item.status}"],
                "extendedProps": {"virtual": False, "status": item.status},
            })
    return jsonify(out)


@bp.route("/lessons/<int:lesson_id>/autosave", methods=["POST"])
def autosave(lesson_id):
    lesson = db.session.get(Lesson, lesson_id)
    if lesson is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    data = request.get_json(silent=True) or {}
    try:
        client_version = int(data.get("version"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "version"}), 400
    if client_version != lesson.version:
        return jsonify({"ok": False, "error": "conflict", "version": lesson.version}), 409
    changed = False
    for field, limit in NOTEBOOK_FIELDS.items():
        if field in data:
            value = (str(data[field] or "")).strip()[:limit] or None
            if getattr(lesson, field) != value:
                setattr(lesson, field, value)
                changed = True
    if changed:
        lesson.version += 1  # notebook text is not audited per keystroke
        db.session.commit()
    return jsonify({"ok": True, "version": lesson.version})
