import csv
import io
from datetime import date, timedelta

from flask import Blueprint, Response, abort, render_template, request
from sqlalchemy import func

from ..extensions import db
from ..i18n import LANGS, get_lang
from ..models import Assessment, Attendance, Charge, Group, Lesson, Payment, Settings, Student
from ..services import attendance as attendance_svc
from ..services import billing, stats
from ..services.pdf import Report
from ..utils import add_months, format_money, month_start, today_rome
from ._helpers import get_or_404

bp = Blueprint("reports", __name__)

ATT_CODES = ("present", "late", "absent", "excused", "makeup", "cancelled")


def _num(d):
    """Decimal('8.50') -> '8.5', Decimal('9.00') -> '9'."""
    return format(d.normalize(), "f")


def _lang(default=None):
    lang = request.args.get("lang")
    if lang in LANGS:
        return lang
    return default if default in LANGS else get_lang()


def _pdf(report, filename):
    data = report.output_bytes()
    disposition = "attachment" if request.args.get("download") else "inline"
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})


def _month_arg():
    raw = request.args.get("month") or ""
    try:
        y, m = (int(x) for x in raw.split("-"))
        return date(y, m, 1)
    except ValueError:
        return month_start(today_rome())


@bp.route("/reports")
def index():
    groups = Group.query.order_by(Group.active.desc(), func.lower(Group.name)).all()
    students = Student.query.filter(Student.status != "archived").order_by(func.lower(Student.name)).all()
    months = [add_months(month_start(today_rome()), -i) for i in range(12)]
    return render_template("reports/index.html", groups=groups, students=students, months=months, nav="reports")


# ── Receipt ──────────────────────────────────────────────────────────────────

@bp.route("/payments/<int:payment_id>/receipt.pdf")
def receipt(payment_id):
    settings = Settings.get()
    p = get_or_404(Payment, payment_id)
    s = p.student
    lang = _lang(s.message_language)
    r = Report(lang, settings, r_t(lang, "pdf_receipt_title"), subtitle=p.receipt_no)
    if p.voided_at:
        r.para(r.t("pdf_receipt_voided", reason=p.void_reason or ""), size=11, color=(185, 28, 28))
    r.kv([
        (r.t("receipt_no"), p.receipt_no),
        (r.t("date"), r.date(p.paid_on)),
        (r.t("received_from"), s.name + (f" — {s.guardian_name}" if s.guardian_name else "")),
        (r.t("amount"), r.money(p.amount_cents)),
        (r.t("method"), r.t("method_" + p.method)),
        (r.t("note"), p.note or ""),
    ], label_w=40)
    acc = billing.account(s, settings=settings)
    r.h2(r.t("pdf_balance_after"))
    r.kv([
        (r.t("remaining_lessons"), str(acc.remaining)),
        (r.t("outstanding"), r.money(max(0, acc.outstanding_cents))),
    ], label_w=40)
    if settings.receipt_footer:
        r.para(settings.receipt_footer, size=9, color=(100, 116, 139))
    r.signature(r.t("signature"))
    return _pdf(r, f"{p.receipt_no}.pdf")


def r_t(lang, key, **kw):
    from ..i18n import translate

    return translate(key, lang, **kw)


# ── Student report & statement ───────────────────────────────────────────────

@bp.route("/students/<int:student_id>/report.pdf")
def student_report(student_id):
    settings = Settings.get()
    s = get_or_404(Student, student_id)
    lang = _lang()
    acc = billing.account(s, settings=settings)
    att = attendance_svc.attendance_stats(student_id=s.id)
    r = Report(lang, settings, r_t(lang, "pdf_student_report_title"), subtitle=s.name)
    r.stats([
        (r.t("attendance_rate"), f"{att['rate']}%" if att["rate"] is not None else "—"),
        (r.t("lessons_attended"), att["attended"]),
        (r.t("remaining_lessons"), acc.remaining),
        (r.t("outstanding"), r.money(max(0, acc.outstanding_cents))),
    ])
    r.h2(r.t("personal_info"))
    r.kv([
        (r.t("full_name"), s.name),
        (r.t("school_grade"), " — ".join(x for x in [s.school_grade, s.school] if x)),
        (r.t("groups"), ", ".join(g.name for g in s.active_groups())),
        (r.t("phone"), s.phone or ""),
        (r.t("guardian"), " ".join(x for x in [s.guardian_name, s.guardian_phone] if x)),
        (r.t("enrollment_date"), r.date(s.enrollment_date) if s.enrollment_date else ""),
        (r.t("status"), r.t("status_" + s.status)),
    ])
    c = att["counts"]
    r.h2(r.t("attendance"))
    r.table(
        [r.t("att_" + k) for k in ATT_CODES],
        [[c[k] for k in ATT_CODES]],
        [1] * len(ATT_CODES), aligns=["C"] * len(ATT_CODES), zebra=False,
    )
    history = (
        db.session.query(Attendance, Lesson).join(Lesson, Lesson.id == Attendance.lesson_id)
        .filter(Attendance.student_id == s.id).order_by(Lesson.date.desc(), Lesson.time.desc()).limit(20).all()
    )
    if history:
        r.h2(r.t("recent_lessons"))
        r.table(
            [r.t("date"), r.t("status"), r.t("homework_short"), r.t("topics")],
            [[r.date(l.date, "numeric"), r.t("att_" + a.status),
              r.t("hw_" + a.homework_status) if a.homework_status else "",
              (l.topics or l.title or "").replace("\n", " ")] for a, l in history],
            [2.2, 2, 1.8, 7],
        )
    assessments = Assessment.query.filter_by(student_id=s.id).order_by(Assessment.taken_on.desc()).limit(15).all()
    if assessments:
        r.h2(r.t("grades"))
        r.table(
            [r.t("date"), r.t("title"), r.t("score"), "%"],
            [[r.date(a.taken_on, "numeric"), a.title, f"{_num(a.score)} / {_num(a.max_score)}", f"{a.percent:.0f}%"]
             for a in assessments],
            [2.2, 6, 2.2, 1.4],
        )
    if s.notes:
        r.h2(r.t("teacher_notes"))
        r.para(s.notes)
    return _pdf(r, f"report-{s.id}.pdf")


@bp.route("/students/<int:student_id>/statement.pdf")
def statement(student_id):
    settings = Settings.get()
    s = get_or_404(Student, student_id)
    lang = _lang(s.message_language)
    acc = billing.account(s, settings=settings)
    r = Report(lang, settings, r_t(lang, "pdf_statement_title"), subtitle=s.name)
    r.stats([
        (r.t("purchased_lessons"), acc.purchased + acc.adjusted),
        (r.t("consumed_lessons"), acc.consumed),
        (r.t("remaining_lessons"), acc.remaining),
        (r.t("outstanding"), r.money(acc.outstanding_cents)),
    ])
    rows = []
    entries = []
    for c in Charge.query.filter_by(student_id=s.id, voided_at=None).all():
        entries.append((c.charged_on, c.id, "c", c))
    for p in Payment.query.filter_by(student_id=s.id, voided_at=None).all():
        entries.append((p.paid_on, p.id, "p", p))
    entries.sort(key=lambda e: (e[0], e[2], e[1]))
    balance = 0
    for d, _id, kind, obj in entries:
        if kind == "c":
            balance += obj.amount_cents
            desc = obj.description or r.t("lessons_n", n=obj.lessons)
            rows.append([r.date(d, "numeric"), desc, str(obj.lessons or ""), r.money(obj.amount_cents), "", r.money(balance)])
        else:
            balance -= obj.amount_cents
            rows.append([r.date(d, "numeric"), f"{r.t('payment')} {obj.receipt_no}", "", "", r.money(obj.amount_cents),
                         r.money(balance)])
    r.h2(r.t("transactions"))
    if rows:
        r.table(
            [r.t("date"), r.t("description"), r.t("lessons"), r.t("charged"), r.t("paid"), r.t("balance")],
            rows, [2.2, 5.5, 1.5, 2.3, 2.3, 2.3],
            aligns=[r.start_align, r.start_align, "C", r.end_align, r.end_align, r.end_align],
        )
    else:
        r.para(r.t("no_transactions"))
    r.kv([
        (r.t("charged"), r.money(acc.charged_cents)),
        (r.t("paid"), r.money(acc.paid_cents)),
        (r.t("outstanding"), r.money(acc.outstanding_cents)),
        (r.t("owed_lessons"), str(acc.owed_lessons) if acc.owed_lessons else ""),
    ])
    return _pdf(r, f"statement-{s.id}.pdf")


# ── Attendance sheet ─────────────────────────────────────────────────────────

@bp.route("/reports/attendance.pdf")
def attendance_sheet():
    settings = Settings.get()
    lang = _lang()
    first = _month_arg()
    last = add_months(first, 1) - timedelta(days=1)
    group_id = request.args.get("group", type=int)
    student_id = request.args.get("student", type=int)
    q = Lesson.query.filter(Lesson.date >= first, Lesson.date <= last)
    if group_id:
        group = get_or_404(Group, group_id)
        q = q.filter(Lesson.group_id == group.id)
        subject = group.name
    elif student_id:
        student = get_or_404(Student, student_id)
        q = q.join(Attendance, Attendance.lesson_id == Lesson.id).filter(Attendance.student_id == student.id)
        subject = student.name
    else:
        abort(400)
    lessons = q.order_by(Lesson.date, Lesson.time).all()
    students = {}
    for l in lessons:
        for s in l.participants():
            students[s.id] = s
    if group_id and not students:
        for s in group.members(on=last):
            students[s.id] = s
    students = sorted(students.values(), key=lambda s: s.name.lower())
    if student_id:
        students = [s for s in students if s.id == student_id]

    landscape = len(lessons) > 8
    r = Report(lang, settings, r_t(lang, "pdf_attendance_title"),
               subtitle=f"{subject} · {r_t(lang, 'month')}: {fmt_month(first, lang)}", orientation="L" if landscape else "P")
    headers = [r.t("student")] + [f"{l.date.day}/{l.date.month}" for l in lessons] + [r.t("attended"), "%"]
    rows = []
    for s in students:
        row = [s.name]
        attended = expected = 0
        for l in lessons:
            if l.status == "cancelled":
                row.append("—")
                continue
            a = l.attendance_for(s.id)
            if a is None:
                row.append("")
                continue
            row.append(r.t("code_" + a.status))
            if a.status in ("present", "late", "makeup"):
                attended += 1
            if a.status != "cancelled":
                expected += 1
        row.append(str(attended))
        row.append(f"{round(attended / expected * 100)}%" if expected else "—")
        rows.append(row)
    widths = [5] + [1.1] * len(lessons) + [1.6, 1.4]
    aligns = [r.start_align] + ["C"] * (len(lessons) + 2)
    if rows:
        r.table(headers, rows, widths, aligns=aligns, font_size=8)
    else:
        r.para(r.t("no_lessons_period"))
    r.para(" · ".join(f"{r.t('code_' + k)} = {r.t('att_' + k)}" for k in ATT_CODES) + " · — = " + r.t("lesson_cancelled"),
           size=8, color=(100, 116, 139))
    return _pdf(r, f"attendance-{first:%Y-%m}.pdf")


def fmt_month(d, lang):
    from ..i18n import fmt_date

    return fmt_date(d, lang=lang, style="month")


# ── Group report ─────────────────────────────────────────────────────────────

@bp.route("/groups/<int:group_id>/report.pdf")
def group_report(group_id):
    settings = Settings.get()
    g = get_or_404(Group, group_id)
    lang = _lang()
    members = g.members()
    accounts = billing.accounts_for(members, settings=settings)
    r = Report(lang, settings, r_t(lang, "pdf_group_report_title"), subtitle=g.name)
    r.kv([
        (r.t("subject"), g.subject or ""),
        (r.t("level"), g.level or ""),
        (r.t("members"), str(len(members))),
    ])
    r.h2(r.t("members"))
    rows = []
    for s in members:
        att = attendance_svc.attendance_stats(student_id=s.id)
        a = accounts[s.id]
        rows.append([s.name, f"{att['rate']}%" if att["rate"] is not None else "—", str(a.remaining),
                     r.money(max(0, a.outstanding_cents))])
    if rows:
        r.table([r.t("student"), r.t("attendance_rate"), r.t("remaining_lessons"), r.t("outstanding")], rows,
                [6, 2.5, 2.5, 3], aligns=[r.start_align, "C", "C", r.end_align])
    lessons = Lesson.query.filter_by(group_id=g.id).filter(Lesson.status != "cancelled") \
        .order_by(Lesson.date.desc(), Lesson.time.desc()).limit(15).all()
    if lessons:
        r.h2(r.t("recent_lessons"))
        r.table(
            [r.t("date"), r.t("present_count"), r.t("topics")],
            [[r.date(l.date, "numeric"),
              str(sum(1 for a in l.attendance if a.status in ("present", "late", "makeup"))),
              (l.topics or l.title or "").replace("\n", " ")] for l in lessons],
            [2.2, 1.8, 9],
        )
    return _pdf(r, f"group-{g.id}.pdf")


# ── Lesson summary ───────────────────────────────────────────────────────────

@bp.route("/lessons/<int:lesson_id>/summary.pdf")
def lesson_summary(lesson_id):
    settings = Settings.get()
    l = get_or_404(Lesson, lesson_id)
    lang = _lang()
    r = Report(lang, settings, r_t(lang, "pdf_lesson_summary_title"), subtitle=l.display_title)
    r.kv([
        (r.t("date"), f"{r.date(l.date)} · {l.time}–{l.end_time}"),
        (r.t("title"), l.title or ""),
        (r.t("lesson_kind"), r.t("kind_" + l.kind)),
        (r.t("status"), r.t("lesson_status_" + l.status) + (f" — {l.cancel_reason}" if l.cancel_reason else "")),
    ])
    for field, key in (("topics", "topics"), ("notes", "lesson_notes"), ("homework", "homework"),
                       ("observations", "observations")):
        value = getattr(l, field)
        if value:
            r.h2(r.t(key))
            r.para(value)
    parts = l.participants()
    if parts:
        r.h2(r.t("attendance"))
        rows = []
        for s in parts:
            a = l.attendance_for(s.id)
            rows.append([s.name, r.t("att_" + a.status) if a else "—",
                         r.t("hw_" + a.homework_status) if a and a.homework_status else "",
                         ("★" * a.performance) if a and a.performance else "", (a.note or "") if a else ""])
        r.table([r.t("student"), r.t("status"), r.t("homework_short"), r.t("performance"), r.t("note")], rows,
                [4.5, 2.2, 2, 2, 5])
    return _pdf(r, f"lesson-{l.id}.pdf")


# ── Monthly financial summary ────────────────────────────────────────────────

@bp.route("/reports/finance.pdf")
def finance():
    settings = Settings.get()
    lang = _lang()
    first = _month_arg()
    last = add_months(first, 1) - timedelta(days=1)
    totals = stats.month_totals(first, last)
    payments = Payment.query.filter(Payment.voided_at.is_(None), Payment.paid_on >= first, Payment.paid_on <= last) \
        .order_by(Payment.paid_on, Payment.id).all()
    by_method = {}
    for p in payments:
        by_method[p.method] = by_method.get(p.method, 0) + p.amount_cents
    students = Student.query.filter(Student.status != "archived").order_by(func.lower(Student.name)).all()
    accounts = billing.accounts_for(students, settings=settings)
    debtors = [s for s in students if accounts[s.id].outstanding_cents > 0 or accounts[s.id].owed_lessons > 0]
    att = attendance_svc.attendance_stats(since=first)

    r = Report(lang, settings, r_t(lang, "pdf_finance_title"), subtitle=fmt_month(first, lang))
    r.stats([
        (r.t("received"), r.money(totals["received"])),
        (r.t("charged"), r.money(totals["charged"])),
        (r.t("payments_count"), totals["payments"]),
        (r.t("total_outstanding"), r.money(sum(max(0, accounts[s.id].outstanding_cents) for s in students))),
    ])
    if by_method:
        r.h2(r.t("by_method"))
        r.table([r.t("method"), r.t("amount")],
                [[r.t("method_" + m), r.money(c)] for m, c in sorted(by_method.items())],
                [6, 4], aligns=[r.start_align, r.end_align])
    r.h2(r.t("payments"))
    if payments:
        r.table(
            [r.t("date"), r.t("receipt_no"), r.t("student"), r.t("method"), r.t("amount")],
            [[r.date(p.paid_on, "numeric"), p.receipt_no, p.student.name, r.t("method_" + p.method),
              r.money(p.amount_cents)] for p in payments],
            [2.2, 2.6, 5, 2, 2.6], aligns=[r.start_align] * 4 + [r.end_align],
        )
    else:
        r.para(r.t("no_transactions"))
    if debtors:
        r.h2(r.t("to_collect"))
        r.table(
            [r.t("student"), r.t("owed_lessons"), r.t("outstanding"), r.t("status")],
            [[s.name, str(accounts[s.id].owed_lessons or ""), r.money(max(0, accounts[s.id].outstanding_cents)),
              r.t("overdue") if accounts[s.id].overdue else ""] for s in debtors],
            [6, 2.5, 3, 2.5], aligns=[r.start_align, "C", r.end_align, "C"],
        )
    if att["expected"]:
        r.h2(r.t("attendance"))
        r.kv([(r.t("attendance_rate"), f"{att['rate']}%"), (r.t("lessons_attended"), str(att["attended"]))])
    return _pdf(r, f"finance-{first:%Y-%m}.pdf")


# ── CSV exports ──────────────────────────────────────────────────────────────

def _csv(rows, filename):
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens UTF-8 (Arabic) correctly
    writer = csv.writer(buf, delimiter=";")
    writer.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _csv_safe(value):
    """Prevent spreadsheet formula injection."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


@bp.route("/export/students.csv")
def export_students():
    settings = Settings.get()
    students = Student.query.order_by(func.lower(Student.name)).all()
    accounts = billing.accounts_for(students, settings=settings)
    rows = [["id", "name", "phone", "guardian_name", "guardian_phone", "school_grade", "school", "status",
             "billing_mode", "enrollment_date", "remaining_lessons", "outstanding"]]
    for s in students:
        a = accounts[s.id]
        rows.append([s.id, _csv_safe(s.name), _csv_safe(s.phone), _csv_safe(s.guardian_name), _csv_safe(s.guardian_phone),
                     _csv_safe(s.school_grade), _csv_safe(s.school), s.status, s.billing_mode, s.enrollment_date or "",
                     a.remaining, format_money(a.outstanding_cents, settings.currency)])
    return _csv(rows, "students.csv")


@bp.route("/export/payments.csv")
def export_payments():
    settings = Settings.get()
    rows = [["date", "receipt_no", "student", "amount", "method", "note", "voided", "void_reason"]]
    for p in Payment.query.order_by(Payment.paid_on, Payment.id).all():
        rows.append([p.paid_on, p.receipt_no, _csv_safe(p.student.name), format_money(p.amount_cents, settings.currency),
                     p.method, _csv_safe(p.note), "yes" if p.voided_at else "", _csv_safe(p.void_reason)])
    return _csv(rows, "payments.csv")
