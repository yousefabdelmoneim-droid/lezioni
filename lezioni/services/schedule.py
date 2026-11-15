"""Recurring slots, agenda building, materialization and conflict detection."""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import Attendance, Lesson, ScheduleSlot, Settings
from ..utils import overlaps, time_to_minutes
from . import audit


@dataclass
class Occurrence:
    """A planned slot occurrence that has no lesson row yet."""

    slot: ScheduleSlot
    date: object
    time: str
    duration_min: int

    @property
    def title(self):
        return self.slot.title

    @property
    def color(self):
        return self.slot.color

    @property
    def is_virtual(self):
        return True

    @property
    def group_id(self):
        return self.slot.group_id

    @property
    def student_id(self):
        return self.slot.student_id

    @property
    def end_time(self):
        total = min(time_to_minutes(self.time) + self.duration_min, 23 * 60 + 59)
        return f"{total // 60:02d}:{total % 60:02d}"


def active_slots():
    return (
        ScheduleSlot.query.options(joinedload(ScheduleSlot.group), joinedload(ScheduleSlot.student))
        .filter(ScheduleSlot.active.is_(True))
        .all()
    )


def _slot_is_live(slot):
    if slot.group_id:
        return slot.group is not None and slot.group.active
    return slot.student is not None and slot.student.status == "active"


def virtual_occurrences(start, end, slots=None):
    """Slot occurrences in [start, end] that have not been materialized."""
    slots = [s for s in (slots if slots is not None else active_slots()) if _slot_is_live(s)]
    if not slots:
        return []
    taken = {
        (sid, d)
        for sid, d in db.session.query(Lesson.slot_id, Lesson.slot_date)
        .filter(Lesson.slot_id.isnot(None), Lesson.slot_date >= start, Lesson.slot_date <= end)
        .all()
    }
    # A lesson planned by hand for the same student/group at the same time
    # replaces the occurrence (avoids duplicates, as the old calendar did).
    manual = {
        (gid, sid, d, t)
        for gid, sid, d, t in db.session.query(Lesson.group_id, Lesson.student_id, Lesson.date, Lesson.time)
        .filter(Lesson.date >= start, Lesson.date <= end)
        .all()
    }
    out = []
    for slot in slots:
        first = max(start, slot.valid_from)
        last = min(end, slot.valid_until) if slot.valid_until else end
        if first > last:
            continue
        d = first + timedelta(days=(slot.weekday - first.weekday()) % 7)
        while d <= last:
            if (slot.id, d) not in taken and (slot.group_id, slot.student_id, d, slot.start_time) not in manual:
                out.append(Occurrence(slot=slot, date=d, time=slot.start_time, duration_min=slot.duration_min))
            d += timedelta(days=7)
    return out


def lessons_between(start, end, include_cancelled=True):
    q = (
        Lesson.query.options(
            joinedload(Lesson.group), joinedload(Lesson.student),
            selectinload(Lesson.attendance).joinedload(Attendance.student),
        )
        .filter(Lesson.date >= start, Lesson.date <= end)
    )
    if not include_cancelled:
        q = q.filter(Lesson.status != "cancelled")
    return q.all()


def agenda(start, end, include_cancelled=True):
    """Real lessons + virtual occurrences, sorted by date/time."""
    items = list(lessons_between(start, end, include_cancelled)) + virtual_occurrences(start, end)
    items.sort(key=lambda x: (x.date, x.time))
    return items


def materialize(slot, on_date, user_id=None):
    """Get or create the lesson row for a slot occurrence."""
    lesson = Lesson.query.filter_by(slot_id=slot.id, slot_date=on_date).first()
    if lesson:
        return lesson, False
    lesson = Lesson(
        slot_id=slot.id,
        slot_date=on_date,
        date=on_date,
        time=slot.start_time,
        duration_min=slot.duration_min,
        group_id=slot.group_id,
        student_id=slot.student_id,
        status="scheduled",
        kind="regular",
    )
    db.session.add(lesson)
    try:
        db.session.flush()
    except IntegrityError:  # created by a concurrent request
        db.session.rollback()
        return Lesson.query.filter_by(slot_id=slot.id, slot_date=on_date).first(), False
    audit.log("lesson.create", "lesson", lesson.id, user_id=user_id, summary=f"{slot.title} · {on_date} {slot.start_time}")
    db.session.commit()
    return lesson, True


def conflicts(on_date, start, duration_min, exclude_lesson_id=None, exclude_slot=None):
    """Lessons/occurrences that overlap the given time (one teacher = any overlap)."""
    found = []
    for item in agenda(on_date, on_date, include_cancelled=False):
        if getattr(item, "id", None) is not None and item.id == exclude_lesson_id:
            continue
        if isinstance(item, Occurrence) and exclude_slot is not None and item.slot.id == exclude_slot[0] \
                and item.date == exclude_slot[1]:
            continue
        if overlaps(start, duration_min, item.time, item.duration_min or 60):
            found.append(item)
    return found


def slot_conflicts(weekday, start, duration_min, exclude_slot_id=None):
    found = []
    for slot in active_slots():
        if slot.id == exclude_slot_id or slot.weekday != weekday or not _slot_is_live(slot):
            continue
        if overlaps(start, duration_min, slot.start_time, slot.duration_min):
            found.append(slot)
    return found


def default_duration():
    return Settings.get().default_duration_min
