"""Numbers for the dashboard and reports."""
from datetime import timedelta

from sqlalchemy import func

from ..extensions import db
from ..models import AuditLog, Charge, Group, Payment, Student
from ..utils import add_months, month_start, today_rome
from . import attendance as attendance_svc
from . import billing, schedule


def monthly_revenue(months=6, today=None):
    """[(month_start_date, cents)] for the last N months, oldest first (payments received)."""
    today = today or today_rome()
    start = add_months(month_start(today), -(months - 1))
    rows = (
        db.session.query(Payment.paid_on, Payment.amount_cents)
        .filter(Payment.voided_at.is_(None), Payment.paid_on >= start)
        .all()
    )
    buckets = {add_months(start, i): 0 for i in range(months)}
    for paid_on, cents in rows:
        key = month_start(paid_on)
        if key in buckets:
            buckets[key] += cents
    return list(buckets.items())


def month_totals(first, last):
    received = (
        db.session.query(func.coalesce(func.sum(Payment.amount_cents), 0))
        .filter(Payment.voided_at.is_(None), Payment.paid_on >= first, Payment.paid_on <= last)
        .scalar()
    )
    charged = (
        db.session.query(func.coalesce(func.sum(Charge.amount_cents), 0))
        .filter(Charge.voided_at.is_(None), Charge.charged_on >= first, Charge.charged_on <= last)
        .scalar()
    )
    count = (
        db.session.query(func.count(Payment.id))
        .filter(Payment.voided_at.is_(None), Payment.paid_on >= first, Payment.paid_on <= last)
        .scalar()
    )
    return {"received": int(received), "charged": int(charged), "payments": int(count)}


def dashboard(settings):
    today = today_rome()
    students = Student.query.filter(Student.status != "archived").order_by(func.lower(Student.name)).all()
    active = [s for s in students if s.status == "active"]
    accounts = billing.accounts_for(students, settings=settings, today=today)

    outstanding_cents = sum(max(0, a.outstanding_cents) for a in accounts.values())
    owed_lessons = sum(a.owed_lessons for a in accounts.values())
    to_collect = [s for s in students if accounts[s.id].owed_lessons > 0 or accounts[s.id].outstanding_cents > 0]
    to_collect.sort(key=lambda s: (not accounts[s.id].overdue, -accounts[s.id].owed_lessons,
                                   -accounts[s.id].outstanding_cents))
    low_balance = [s for s in students if accounts[s.id].low_balance]

    todays = schedule.agenda(today, today)
    upcoming = [i for i in schedule.agenda(today + timedelta(days=1), today + timedelta(days=7), include_cancelled=False)]

    first = month_start(today)
    this_month = month_totals(first, today)

    return {
        "students": students,
        "accounts": accounts,
        "active_students": len(active),
        "active_groups": Group.query.filter_by(active=True).count(),
        "todays": todays,
        "upcoming": upcoming[:8],
        "upcoming_count": len(upcoming),
        "month_received": this_month["received"],
        "outstanding_cents": outstanding_cents,
        "owed_lessons": owed_lessons,
        "to_collect": to_collect[:8],
        "to_collect_count": len(to_collect),
        "overdue_count": sum(1 for s in students if accounts[s.id].overdue),
        "low_balance": low_balance,
        "revenue": monthly_revenue(6, today),
        "attendance": attendance_svc.attendance_stats(since=today - timedelta(days=30)),
        "activity": AuditLog.query.order_by(AuditLog.at.desc()).limit(8).all(),
    }
