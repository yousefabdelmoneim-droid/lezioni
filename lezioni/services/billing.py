"""Lesson credits and money.

Two separate ledgers per student:

* lesson credits (``credit_entries``): remaining = purchased − consumed ± adjustments
* money (``charges`` vs ``payments``): outstanding = charged − paid

Every function that writes commits its own transaction, so a financial
operation is either fully stored or not stored at all.
"""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Attendance, Charge, CreditEntry, Lesson, Payment, Settings, utcnow
from ..utils import format_money, today_rome
from . import audit


class BillingError(Exception):
    def __init__(self, code, **params):
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass
class Account:
    purchased: int = 0
    consumed: int = 0
    adjusted: int = 0
    charged_cents: int = 0
    paid_cents: int = 0
    last_payment_on: date | None = None
    owed_since: date | None = None
    money_due_since: date | None = None
    overdue: bool = False
    low_balance: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def remaining(self):
        return self.purchased - self.consumed + self.adjusted

    @property
    def owed_lessons(self):
        return max(0, -self.remaining)

    @property
    def outstanding_cents(self):
        return self.charged_cents - self.paid_cents

    @property
    def settled(self):
        return self.outstanding_cents <= 0 and self.owed_lessons == 0


# ── Reading ──────────────────────────────────────────────────────────────────

def _credit_totals(student_ids=None):
    q = db.session.query(CreditEntry.student_id, CreditEntry.kind, func.coalesce(func.sum(CreditEntry.delta), 0)).filter(
        CreditEntry.reversed_at.is_(None), CreditEntry.kind != "reversal"
    )
    if student_ids is not None:
        q = q.filter(CreditEntry.student_id.in_(student_ids))
    out = {}
    for sid, kind, total in q.group_by(CreditEntry.student_id, CreditEntry.kind):
        out.setdefault(sid, {})[kind] = int(total)
    return out


def _money_totals(student_ids=None):
    cq = db.session.query(Charge.student_id, func.coalesce(func.sum(Charge.amount_cents), 0)).filter(
        Charge.voided_at.is_(None)
    )
    pq = db.session.query(
        Payment.student_id, func.coalesce(func.sum(Payment.amount_cents), 0), func.max(Payment.paid_on)
    ).filter(Payment.voided_at.is_(None))
    if student_ids is not None:
        cq = cq.filter(Charge.student_id.in_(student_ids))
        pq = pq.filter(Payment.student_id.in_(student_ids))
    charged = {sid: int(t) for sid, t in cq.group_by(Charge.student_id)}
    paid = {sid: (int(t), last) for sid, t, last in pq.group_by(Payment.student_id)}
    return charged, paid


def _owed_since(student_id, available_credits):
    """Date of the first lesson consumed without a credit (FIFO)."""
    rows = (
        db.session.query(Lesson.date)
        .select_from(CreditEntry)
        .join(Attendance, Attendance.id == CreditEntry.attendance_id)
        .join(Lesson, Lesson.id == Attendance.lesson_id)
        .filter(
            CreditEntry.student_id == student_id,
            CreditEntry.kind == "consume",
            CreditEntry.reversed_at.is_(None),
        )
        .order_by(Lesson.date, Lesson.time, CreditEntry.id)
        .all()
    )
    k = max(0, available_credits)
    if k < len(rows):
        return rows[k][0]
    return None


def _money_due_since(student_id, paid_cents):
    remaining_paid = paid_cents
    charges = (
        Charge.query.filter_by(student_id=student_id, voided_at=None)
        .order_by(Charge.charged_on, Charge.id)
        .all()
    )
    for c in charges:
        if remaining_paid >= c.amount_cents:
            remaining_paid -= c.amount_cents
            continue
        return c.charged_on
    return None


def accounts_for(students, settings=None, today=None):
    """Account for each student, keyed by student id (3 aggregate queries + detail for debtors)."""
    settings = settings or Settings.get()
    today = today or today_rome()
    ids = [s.id for s in students]
    if not ids:
        return {}
    credits = _credit_totals(ids)
    charged, paid = _money_totals(ids)
    result = {}
    by_id = {s.id: s for s in students}
    for sid in ids:
        c = credits.get(sid, {})
        acc = Account(
            purchased=c.get("opening", 0) + c.get("purchase", 0),
            consumed=-c.get("consume", 0),
            adjusted=c.get("adjust", 0),
            charged_cents=charged.get(sid, 0),
            paid_cents=paid.get(sid, (0, None))[0],
            last_payment_on=paid.get(sid, (0, None))[1],
        )
        if acc.remaining < 0:
            acc.owed_since = _owed_since(sid, acc.purchased + acc.adjusted)
        if acc.outstanding_cents > 0:
            acc.money_due_since = _money_due_since(sid, acc.paid_cents)
        limit = settings.payment_due_days
        acc.overdue = any(
            d is not None and (today - d).days > limit for d in (acc.owed_since, acc.money_due_since)
        )
        student = by_id[sid]
        acc.low_balance = (
            student.billing_mode == "prepaid"
            and student.status == "active"
            and acc.remaining <= settings.low_balance_threshold
        )
        result[sid] = acc
    return result


def account(student, settings=None, today=None):
    return accounts_for([student], settings=settings, today=today)[student.id]


def price_for(student, settings=None):
    settings = settings or Settings.get()
    return student.price_per_lesson_cents if student.price_per_lesson_cents is not None else settings.default_price_cents


def ledger(student):
    """Chronological money + credit history for one student (newest first)."""
    items = []
    for c in Charge.query.filter_by(student_id=student.id).all():
        items.append({"type": "charge", "date": c.charged_on, "at": c.created_at, "obj": c})
    for p in Payment.query.filter_by(student_id=student.id).all():
        items.append({"type": "payment", "date": p.paid_on, "at": p.created_at, "obj": p})
    entries = {e.id: e for e in CreditEntry.query.filter_by(student_id=student.id).all()}
    for e in entries.values():
        if e.kind in ("purchase", "consume"):
            continue  # shown through charges / attendance history
        if e.kind == "reversal":
            original = entries.get(e.reverses_id)
            if original is None or original.kind not in ("opening", "adjust"):
                continue  # voided charges and attendance changes are shown elsewhere
        items.append({"type": "credit", "date": e.created_at.date(), "at": e.created_at, "obj": e})
    items.sort(key=lambda i: (i["date"], i["at"] or utcnow()), reverse=True)
    return items


# ── Writing ──────────────────────────────────────────────────────────────────

def _money(cents):
    return format_money(cents, Settings.get().currency)


def next_receipt_no(on):
    prefix = f"R{on.year}-"
    last = (
        db.session.query(Payment.receipt_no)
        .filter(Payment.receipt_no.like(prefix + "%"))
        .order_by(Payment.receipt_no.desc())
        .first()
    )
    seq = int(last[0].split("-")[1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def _check_key(key):
    key = (key or "").strip()
    if not key or len(key) > 60:
        raise BillingError("missing_key")
    return key


def collect(
    student,
    *,
    idempotency_key,
    lessons=0,
    sale_amount_cents=None,
    unit_price_cents=None,
    package=None,
    description=None,
    paid_cents=0,
    method="cash",
    paid_on=None,
    note=None,
    user_id=None,
):
    """Sell lessons and/or record money received, atomically.

    Returns ``(charge, payment, created)``. When the same idempotency key was
    already used, nothing is written and the existing rows are returned with
    ``created=False``.
    """
    key = _check_key(idempotency_key)
    existing_c = Charge.query.filter_by(idempotency_key=key + ":c").first()
    existing_p = Payment.query.filter_by(idempotency_key=key + ":p").first()
    if existing_c or existing_p:
        return existing_c, existing_p, False

    if package is not None:
        lessons = package.lessons
        sale_amount_cents = package.price_cents
        description = description or package.name
    lessons = int(lessons or 0)
    if lessons < 0:
        raise BillingError("invalid_lessons")
    if sale_amount_cents is None and lessons and unit_price_cents is not None:
        sale_amount_cents = lessons * unit_price_cents
    if lessons and sale_amount_cents is None:
        raise BillingError("missing_price")
    sale_amount_cents = int(sale_amount_cents or 0)
    paid_cents = int(paid_cents or 0)
    if sale_amount_cents < 0 or paid_cents < 0:
        raise BillingError("invalid_amount")
    if method not in ("cash", "bank", "card", "other"):
        raise BillingError("invalid_method")
    if not lessons and not sale_amount_cents and not paid_cents:
        raise BillingError("nothing_to_save")

    today = today_rome()
    paid_on = paid_on or today
    charge = payment = None
    try:
        if lessons or sale_amount_cents:
            charge = Charge(
                student_id=student.id,
                description=(description or "")[:200],
                lessons=lessons,
                unit_price_cents=unit_price_cents if package is None else None,
                amount_cents=sale_amount_cents,
                package_id=package.id if package is not None else None,
                charged_on=paid_on if paid_cents else today,
                idempotency_key=key + ":c",
                created_by_id=user_id,
            )
            db.session.add(charge)
            db.session.flush()
            if lessons:
                db.session.add(
                    CreditEntry(
                        student_id=student.id,
                        delta=lessons,
                        kind="purchase",
                        charge_id=charge.id,
                        note=charge.description,
                        created_by_id=user_id,
                    )
                )
            audit.log(
                "charge.create", "charge", charge.id, student_id=student.id, user_id=user_id,
                summary=f"{student.name} · +{lessons} · {_money(sale_amount_cents)}",
                data={"lessons": lessons, "amount_cents": sale_amount_cents, "package_id": charge.package_id},
            )
        if paid_cents:
            payment = Payment(
                student_id=student.id,
                amount_cents=paid_cents,
                method=method,
                paid_on=paid_on,
                receipt_no=next_receipt_no(paid_on),
                note=(note or "")[:255] or None,
                idempotency_key=key + ":p",
                created_by_id=user_id,
            )
            db.session.add(payment)
            db.session.flush()
            audit.log(
                "payment.create", "payment", payment.id, student_id=student.id, user_id=user_id,
                summary=f"{student.name} · {_money(paid_cents)} · {payment.receipt_no}",
                data={"amount_cents": paid_cents, "method": method, "receipt_no": payment.receipt_no},
            )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing_c = Charge.query.filter_by(idempotency_key=key + ":c").first()
        existing_p = Payment.query.filter_by(idempotency_key=key + ":p").first()
        if existing_c or existing_p:
            return existing_c, existing_p, False
        raise
    return charge, payment, True


def _reverse(entry, note, user_id=None):
    if entry.reversed_at is not None:
        return None
    entry.reversed_at = utcnow()
    rev = CreditEntry(
        student_id=entry.student_id,
        delta=-entry.delta,
        kind="reversal",
        charge_id=entry.charge_id,
        attendance_id=entry.attendance_id,
        reverses_id=entry.id,
        note=(note or "")[:255],
        created_by_id=user_id,
    )
    db.session.add(rev)
    return rev


def void_payment(payment, reason, user_id=None):
    reason = (reason or "").strip()
    if not reason:
        raise BillingError("reason_required")
    if payment.voided_at is not None:
        raise BillingError("already_voided")
    payment.voided_at = utcnow()
    payment.void_reason = reason[:255]
    audit.log(
        "payment.void", "payment", payment.id, student_id=payment.student_id, user_id=user_id,
        summary=f"{payment.student.name} · {payment.receipt_no} · {_money(payment.amount_cents)} · {reason}"[:255],
        data={"amount_cents": payment.amount_cents, "reason": reason},
    )
    db.session.commit()


def void_charge(charge, reason, user_id=None):
    reason = (reason or "").strip()
    if not reason:
        raise BillingError("reason_required")
    if charge.voided_at is not None:
        raise BillingError("already_voided")
    charge.voided_at = utcnow()
    charge.void_reason = reason[:255]
    for e in CreditEntry.query.filter_by(charge_id=charge.id, kind="purchase", reversed_at=None).all():
        _reverse(e, f"void: {reason}", user_id)
    audit.log(
        "charge.void", "charge", charge.id, student_id=charge.student_id, user_id=user_id,
        summary=f"{charge.student.name} · {_money(charge.amount_cents)} · {reason}"[:255],
        data={"amount_cents": charge.amount_cents, "lessons": charge.lessons},
    )
    db.session.commit()


def adjust_credits(student, delta, note, user_id=None):
    delta = int(delta)
    note = (note or "").strip()
    if delta == 0:
        raise BillingError("invalid_lessons")
    if not note:
        raise BillingError("reason_required")
    entry = CreditEntry(student_id=student.id, delta=delta, kind="adjust", note=note[:255], created_by_id=user_id)
    db.session.add(entry)
    db.session.flush()
    audit.log(
        "credit.adjust", "credit", entry.id, student_id=student.id, user_id=user_id,
        summary=f"{student.name} · {delta:+d} · {note}"[:255], data={"delta": delta},
    )
    db.session.commit()
    return entry


def reverse_adjustment(entry, reason, user_id=None):
    if entry.kind not in ("adjust", "opening"):
        raise BillingError("not_reversible")
    if entry.reversed_at is not None:
        raise BillingError("already_voided")
    reason = (reason or "").strip()
    if not reason:
        raise BillingError("reason_required")
    _reverse(entry, reason, user_id)
    audit.log(
        "credit.reverse", "credit", entry.id, student_id=entry.student_id, user_id=user_id,
        summary=reason[:255], data={"delta": entry.delta},
    )
    db.session.commit()


# ── Consumption (called by the attendance service, no commit here) ───────────

def active_consumption(attendance):
    return CreditEntry.query.filter_by(attendance_id=attendance.id, kind="consume", reversed_at=None).first()


def sync_consumption(attendance, chargeable, user_id=None):
    """Make the ledger match ``chargeable`` for one attendance row. Idempotent."""
    current = active_consumption(attendance)
    if chargeable and current is None:
        db.session.add(
            CreditEntry(
                student_id=attendance.student_id,
                delta=-1,
                kind="consume",
                attendance_id=attendance.id,
                created_by_id=user_id,
            )
        )
        return "consumed"
    if not chargeable and current is not None:
        _reverse(current, f"attendance: {attendance.status}", user_id)
        return "released"
    return None
