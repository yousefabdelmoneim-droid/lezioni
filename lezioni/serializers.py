"""JSON shapes for the mobile API. Money is always integer cents; dates ISO 8601."""


def _d(value):
    return value.isoformat() if value is not None else None


def account(acc):
    return {
        "purchased": acc.purchased,
        "consumed": acc.consumed,
        "adjusted": acc.adjusted,
        "remaining": acc.remaining,
        "owed_lessons": acc.owed_lessons,
        "charged_cents": acc.charged_cents,
        "paid_cents": acc.paid_cents,
        "outstanding_cents": acc.outstanding_cents,
        "overdue": acc.overdue,
        "low_balance": acc.low_balance,
        "last_payment_on": _d(acc.last_payment_on),
        "owed_since": _d(acc.owed_since),
        "money_due_since": _d(acc.money_due_since),
    }


def group_ref(g):
    return {"id": g.id, "name": g.name, "color": g.color} if g is not None else None


def student_brief(s, acc=None):
    out = {
        "id": s.id,
        "name": s.name,
        "initials": s.initials,
        "status": s.status,
        "billing_mode": s.billing_mode,
        "school_grade": s.school_grade,
        "phone": s.phone,
        "guardian_name": s.guardian_name,
        "guardian_phone": s.guardian_phone,
        "groups": [group_ref(m.group) for m in s.memberships if m.left_on is None],
    }
    if acc is not None:
        out["account"] = account(acc)
    return out


def student_detail(s, acc, price_cents):
    out = student_brief(s, acc)
    out.update({
        "school": s.school,
        "enrollment_date": _d(s.enrollment_date),
        "price_per_lesson_cents": s.price_per_lesson_cents,
        "effective_price_cents": price_cents,
        "message_language": s.message_language,
        "notes": s.notes,
    })
    return out


def slot(sl):
    return {
        "id": sl.id,
        "weekday": sl.weekday,
        "start_time": sl.start_time,
        "duration_min": sl.duration_min,
        "valid_from": _d(sl.valid_from),
        "valid_until": _d(sl.valid_until),
        "group_id": sl.group_id,
        "student_id": sl.student_id,
        "title": sl.title,
    }


def lesson_brief(lesson):
    present = sum(1 for a in lesson.attendance if a.status in ("present", "late", "makeup"))
    return {
        "virtual": False,
        "id": lesson.id,
        "date": _d(lesson.date),
        "time": lesson.time,
        "end_time": lesson.end_time,
        "duration_min": lesson.duration_min,
        "status": lesson.status,
        "kind": lesson.kind,
        "is_free": lesson.is_free,
        "title": lesson.title,
        "display_title": lesson.display_title,
        "color": lesson.color,
        "group": group_ref(lesson.group),
        "student": {"id": lesson.student.id, "name": lesson.student.name} if lesson.student_id else None,
        "attendance_count": len(lesson.attendance),
        "present_count": present,
    }


def occurrence(o):
    return {
        "virtual": True,
        "slot_id": o.slot.id,
        "date": _d(o.date),
        "time": o.time,
        "end_time": o.end_time,
        "duration_min": o.duration_min,
        "display_title": o.title,
        "color": o.color,
        "group_id": o.group_id,
        "student_id": o.student_id,
    }


def agenda_item(item):
    return occurrence(item) if item.is_virtual else lesson_brief(item)


def attendance(a, consumed):
    if a is None:
        return None
    return {
        "id": a.id,
        "status": a.status,
        "homework_status": a.homework_status,
        "performance": a.performance,
        "note": a.note,
        "consumed": consumed,
    }


def charge(c):
    return {
        "id": c.id,
        "type": "charge",
        "date": _d(c.charged_on),
        "description": c.description,
        "lessons": c.lessons,
        "unit_price_cents": c.unit_price_cents,
        "amount_cents": c.amount_cents,
        "voided": c.voided_at is not None,
        "void_reason": c.void_reason,
        "student_id": c.student_id,
    }


def payment(p):
    return {
        "id": p.id,
        "type": "payment",
        "date": _d(p.paid_on),
        "amount_cents": p.amount_cents,
        "method": p.method,
        "receipt_no": p.receipt_no,
        "note": p.note,
        "voided": p.voided_at is not None,
        "void_reason": p.void_reason,
        "student_id": p.student_id,
        "student_name": p.student.name if p.student else None,
    }


def credit(e):
    return {
        "id": e.id,
        "type": "credit",
        "date": _d(e.created_at.date()) if e.created_at else None,
        "kind": e.kind,
        "delta": e.delta,
        "note": e.note,
        "reversed": e.reversed_at is not None,
    }


def ledger_item(item):
    obj = item["obj"]
    if item["type"] == "charge":
        return charge(obj)
    if item["type"] == "payment":
        return payment(obj)
    return credit(obj)


def assessment(a):
    return {
        "id": a.id,
        "title": a.title,
        "kind": a.kind,
        "taken_on": _d(a.taken_on),
        "score": float(a.score),
        "max_score": float(a.max_score),
        "percent": round(a.percent, 1),
        "note": a.note,
    }


def package(p):
    return {"id": p.id, "name": p.name, "lessons": p.lessons, "price_cents": p.price_cents, "active": p.active}


def settings(s):
    return {
        "teacher_name": s.teacher_name,
        "subject": s.subject,
        "currency": s.currency,
        "language": s.language,
        "default_price_cents": s.default_price_cents,
        "default_duration_min": s.default_duration_min,
        "low_balance_threshold": s.low_balance_threshold,
        "payment_due_days": s.payment_due_days,
        "policy": {st: s.charges_for(st) for st in ("present", "late", "absent", "excused", "makeup", "cancelled")},
    }
