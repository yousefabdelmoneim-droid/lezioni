from datetime import timedelta

from lezioni.extensions import db
from lezioni.models import Group, GroupMember, Lesson, Student
from lezioni.utils import today_rome


def student(name="Marco", **kw):
    s = Student(name=name, weekly_plan=1, lesson_days="", last_reset_date=today_rome(),
                status=kw.pop("status", "active"), billing_mode=kw.pop("billing_mode", "postpaid"), **kw)
    db.session.add(s)
    db.session.commit()
    return s


def group(name="Matematica", members=()):
    g = Group(name=name)
    db.session.add(g)
    db.session.flush()
    for s in members:
        db.session.add(GroupMember(group_id=g.id, student_id=s.id, joined_on=today_rome() - timedelta(days=60)))
    db.session.commit()
    return g


def lesson(on=None, time="18:00", student=None, group=None, **kw):
    l = Lesson(date=on or today_rome(), time=time, student_id=student.id if student else None,
               group_id=group.id if group else None, status=kw.pop("status", "scheduled"), kind="regular",
               duration_min=kw.pop("duration_min", 60), **kw)
    db.session.add(l)
    db.session.commit()
    return l
