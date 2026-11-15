"""Critical financial rules: credits, payments, idempotency, reversals."""
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from lezioni.extensions import db
from lezioni.models import AuditLog, Charge, CreditEntry, Payment, Settings
from lezioni.services import attendance, billing
from lezioni.services.billing import BillingError
from lezioni.utils import format_money, parse_money

from . import factories as f


def attend(lesson, student, status="present"):
    attendance.mark(lesson, [{"student_id": student.id, "status": status}])


def test_spec_example_8_purchased_5_consumed_3_remaining(ctx, today):
    s = f.student(billing_mode="prepaid")
    billing.collect(s, idempotency_key="k1", lessons=8, unit_price_cents=2000, paid_cents=16000)
    for i in range(5):
        attend(f.lesson(on=today - timedelta(days=i + 1), student=s), s)
    acc = billing.account(s)
    assert (acc.purchased, acc.consumed, acc.remaining) == (8, 5, 3)
    assert acc.outstanding_cents == 0


def test_never_deducts_twice_for_same_attendance(ctx, today):
    s = f.student()
    l = f.lesson(on=today, student=s)
    attend(l, s, "present")
    attend(l, s, "present")
    attend(l, s, "late")
    assert billing.account(s).consumed == 1
    # ledger sum and summary always agree
    total = db.session.query(db.func.sum(CreditEntry.delta)).filter_by(student_id=s.id).scalar()
    assert total == billing.account(s).remaining == -1


def test_database_blocks_a_second_active_consumption(ctx, today):
    s = f.student()
    l = f.lesson(on=today, student=s)
    attend(l, s)
    att = l.attendance[0]
    db.session.add(CreditEntry(student_id=s.id, delta=-1, kind="consume", attendance_id=att.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_status_change_releases_and_reconsumes_with_history(ctx, today):
    s = f.student()
    l = f.lesson(on=today, student=s)
    attend(l, s, "present")
    attend(l, s, "excused")  # not chargeable by default
    assert billing.account(s).consumed == 0
    attend(l, s, "absent")  # unexcused absence is chargeable by default
    assert billing.account(s).consumed == 1
    kinds = [e.kind for e in CreditEntry.query.filter_by(student_id=s.id).order_by(CreditEntry.id)]
    assert kinds == ["consume", "reversal", "consume"]


def test_policy_is_configurable(ctx, today):
    st = Settings.get()
    st.charge_absent = False
    db.session.commit()
    s = f.student()
    attend(f.lesson(on=today, student=s), s, "absent")
    assert billing.account(s).consumed == 0


def test_cancelled_and_free_lessons_never_consume(ctx, today):
    s = f.student()
    l1 = f.lesson(on=today, student=s)
    attend(l1, s)
    attendance.cancel_lesson(l1, "teacher sick")
    assert billing.account(s).consumed == 0
    attendance.restore_lesson(l1)
    assert billing.account(s).consumed == 1
    attendance.set_free(l1, True)
    assert billing.account(s).consumed == 0


def test_removing_attendance_releases_credit(ctx, today):
    s = f.student()
    l = f.lesson(on=today, student=s)
    attend(l, s)
    attendance.mark(l, [{"student_id": s.id, "status": ""}])
    assert billing.account(s).consumed == 0
    assert l.attendance == []


def test_group_bulk_attendance(ctx, today):
    a, b, c = f.student("A"), f.student("B"), f.student("C")
    g = f.group(members=[a, b, c])
    l = f.lesson(on=today, group=g)
    attendance.mark(l, [{"student_id": x.id, "status": st} for x, st in ((a, "present"), (b, "late"), (c, "excused"))])
    accs = billing.accounts_for([a, b, c])
    assert [accs[x.id].consumed for x in (a, b, c)] == [1, 1, 0]
    assert l.status == "completed"


def test_duplicate_submission_is_saved_once(ctx):
    s = f.student()
    c1, p1, created1 = billing.collect(s, idempotency_key="same", lessons=4, unit_price_cents=2000, paid_cents=8000)
    c2, p2, created2 = billing.collect(s, idempotency_key="same", lessons=4, unit_price_cents=2000, paid_cents=8000)
    assert created1 and not created2
    assert p1.id == p2.id and c1.id == c2.id
    assert Payment.query.count() == 1 and Charge.query.count() == 1
    assert billing.account(s).purchased == 4


def test_partial_payments_and_outstanding(ctx):
    s = f.student()
    billing.collect(s, idempotency_key="a", lessons=4, unit_price_cents=2000, paid_cents=5000)
    acc = billing.account(s)
    assert (acc.charged_cents, acc.paid_cents, acc.outstanding_cents) == (8000, 5000, 3000)
    billing.collect(s, idempotency_key="b", paid_cents=3000)
    assert billing.account(s).outstanding_cents == 0
    assert billing.account(s).remaining == 4


def test_void_payment_keeps_history_and_audit(ctx):
    s = f.student()
    _, p, _ = billing.collect(s, idempotency_key="v", lessons=1, unit_price_cents=2000, paid_cents=2000)
    billing.void_payment(p, "wrong student")
    assert db.session.get(Payment, p.id) is not None
    assert billing.account(s).outstanding_cents == 2000
    assert AuditLog.query.filter_by(action="payment.void", entity_id=p.id).count() == 1
    with pytest.raises(BillingError):
        billing.void_payment(p, "again")
    with pytest.raises(BillingError):
        billing.void_payment(db.session.get(Payment, p.id), "")


def test_void_charge_removes_its_credits(ctx):
    s = f.student()
    c, _, _ = billing.collect(s, idempotency_key="c", lessons=8, sale_amount_cents=15000)
    assert billing.account(s).remaining == 8
    billing.void_charge(c, "mistake")
    acc = billing.account(s)
    assert acc.remaining == 0 and acc.charged_cents == 0


def test_owed_lessons_are_absorbed_by_next_purchase(ctx, today):
    s = f.student()
    for i in range(3):
        attend(f.lesson(on=today - timedelta(days=i + 1), student=s), s)
    assert billing.account(s).owed_lessons == 3
    billing.collect(s, idempotency_key="pkg", lessons=8, sale_amount_cents=15000, paid_cents=15000)
    acc = billing.account(s)
    assert acc.remaining == 5 and acc.owed_lessons == 0


def test_overdue_after_due_days(ctx, today):
    s = f.student()
    attend(f.lesson(on=today - timedelta(days=45), student=s), s)
    assert billing.account(s).overdue is True
    s2 = f.student("Recent")
    attend(f.lesson(on=today - timedelta(days=3), student=s2), s2)
    assert billing.account(s2).overdue is False


def test_receipt_numbers_are_sequential_and_unique(ctx):
    s = f.student()
    nos = [billing.collect(s, idempotency_key=f"r{i}", paid_cents=1000)[1].receipt_no for i in range(3)]
    year = nos[0][1:5]
    assert nos == [f"R{year}-0001", f"R{year}-0002", f"R{year}-0003"]


def test_adjustment_requires_reason_and_can_be_reversed(ctx):
    s = f.student()
    with pytest.raises(BillingError):
        billing.adjust_credits(s, 1, "")
    e = billing.adjust_credits(s, 1, "free lesson")
    assert billing.account(s).remaining == 1
    billing.reverse_adjustment(e, "typo")
    assert billing.account(s).remaining == 0


def test_rejects_invalid_input(ctx):
    s = f.student()
    for kwargs in ({}, {"lessons": -1}, {"lessons": 2}, {"paid_cents": -5}, {"paid_cents": 100, "method": "bitcoin"}):
        with pytest.raises(BillingError):
            billing.collect(s, idempotency_key="x-" + str(kwargs), **kwargs)
    with pytest.raises(BillingError):
        billing.collect(s, idempotency_key="", paid_cents=100)
    assert Payment.query.count() == 0


@pytest.mark.parametrize("raw,cents", [
    ("20", 2000), ("20,5", 2050), ("20,50", 2050), ("20.50", 2050), ("1.234,56", 123456),
    ("1,234.56", 123456), ("€ 15", 1500), ("0,01", 1), ("", None), ("abc", None), ("-5", None), ("12.345", None),
])
def test_parse_money(raw, cents):
    assert parse_money(raw) == cents


def test_format_money():
    assert format_money(123456) == "€ 1.234,56"
    assert format_money(-500) == "\u200e-€ 5,00"
