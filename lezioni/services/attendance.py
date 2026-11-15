"""Attendance and its link to lesson-credit consumption."""
from ..extensions import db
from ..models import ATTENDANCE_STATUSES, HOMEWORK_STATUSES, Attendance, Lesson, Settings, utcnow
from . import audit, billing


class AttendanceError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def is_chargeable(lesson, status, settings):
    if lesson.status == "cancelled" or lesson.is_free:
        return False
    return settings.charges_for(status)


def mark(lesson, entries, user_id=None, settings=None):
    """Save attendance for several students in one transaction.

    ``entries``: list of dicts with ``student_id``, ``status`` and optional
    ``homework_status``, ``performance``, ``note``. A status of ``None`` /
    ``""`` removes the student's attendance (and releases any credit).
    """
    settings = settings or Settings.get()
    if lesson.status == "cancelled":
        raise AttendanceError("lesson_cancelled")
    changed = 0
    for e in entries:
        sid = int(e["student_id"])
        status = (e.get("status") or "").strip() or None
        if status is not None and status not in ATTENDANCE_STATUSES:
            raise AttendanceError("invalid_status")
        hw = (e.get("homework_status") or "").strip() or None
        if hw is not None and hw not in HOMEWORK_STATUSES:
            hw = None
        perf = e.get("performance")
        try:
            perf = int(perf) if perf not in (None, "") else None
        except (TypeError, ValueError):
            perf = None
        if perf is not None and not 1 <= perf <= 5:
            perf = None
        note = (e.get("note") or "").strip()[:500] or None

        row = lesson.attendance_for(sid)
        if status is None:
            if row is not None:
                billing.sync_consumption(row, False, user_id)
                db.session.flush()
                audit.log("attendance.remove", "attendance", row.id, student_id=sid, user_id=user_id,
                          summary=f"{row.student.name} · {lesson.display_title} · {lesson.date}")
                lesson.attendance.remove(row)  # delete-orphan; ledger rows keep history (FK SET NULL)
                db.session.flush()
                changed += 1
            continue
        if row is None:
            row = Attendance(lesson=lesson, student_id=sid, status=status)
            db.session.add(row)
            db.session.flush()
            before = None
        else:
            before = row.status
        row.status = status
        row.homework_status = hw
        row.performance = perf
        row.note = note
        row.updated_at = utcnow()
        db.session.flush()
        result = billing.sync_consumption(row, is_chargeable(lesson, status, settings), user_id)
        if before != status or result:
            audit.log("attendance.mark", "attendance", row.id, student_id=sid, user_id=user_id,
                      summary=f"{row.student.name} · {lesson.display_title} · {lesson.date}",
                      data={"lesson_id": lesson.id, "from": before, "to": status})
            changed += 1

    if lesson.status == "scheduled" and lesson.attendance:
        lesson.status = "completed"
    db.session.commit()
    return changed


def resync_lesson(lesson, user_id=None, settings=None):
    """Re-apply the policy to every attendance row of a lesson (after cancel / free toggle)."""
    settings = settings or Settings.get()
    for row in lesson.attendance:
        billing.sync_consumption(row, is_chargeable(lesson, row.status, settings), user_id)


def cancel_lesson(lesson, reason, user_id=None):
    if lesson.status == "cancelled":
        return
    lesson.status = "cancelled"
    lesson.cancel_reason = (reason or "").strip()[:255] or None
    lesson.cancelled_at = utcnow()
    resync_lesson(lesson, user_id)
    audit.log("lesson.cancel", "lesson", lesson.id, user_id=user_id,
              summary=" · ".join(x for x in [lesson.display_title, str(lesson.date), lesson.cancel_reason] if x))
    db.session.commit()


def restore_lesson(lesson, user_id=None):
    if lesson.status != "cancelled":
        return
    lesson.status = "completed" if lesson.attendance else "scheduled"
    lesson.cancel_reason = None
    lesson.cancelled_at = None
    resync_lesson(lesson, user_id)
    audit.log("lesson.restore", "lesson", lesson.id, user_id=user_id, summary=f"{lesson.display_title} · {lesson.date}")
    db.session.commit()


def set_free(lesson, is_free, user_id=None):
    lesson.is_free = bool(is_free)
    resync_lesson(lesson, user_id)
    audit.log("lesson.free" if is_free else "lesson.unfree", "lesson", lesson.id, user_id=user_id,
              summary=f"{lesson.display_title} · {lesson.date}")
    db.session.commit()


def attendance_stats(query_filter=None, since=None, student_id=None):
    """Counts per status (excluding cancelled lessons)."""
    from sqlalchemy import func

    q = db.session.query(Attendance.status, func.count(Attendance.id)).join(Lesson)
    q = q.filter(Lesson.status != "cancelled")
    if since is not None:
        q = q.filter(Lesson.date >= since)
    if student_id is not None:
        q = q.filter(Attendance.student_id == student_id)
    counts = {s: 0 for s in ATTENDANCE_STATUSES}
    for status, n in q.group_by(Attendance.status):
        counts[status] = n
    attended = counts["present"] + counts["late"] + counts["makeup"]
    expected = attended + counts["absent"] + counts["excused"]
    rate = round(attended / expected * 100) if expected else None
    return {"counts": counts, "attended": attended, "expected": expected, "rate": rate}
