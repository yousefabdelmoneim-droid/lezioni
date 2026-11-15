from flask import Blueprint, redirect, render_template, request, url_for
from sqlalchemy import func

from ..extensions import db
from ..models import PAYMENT_METHODS, Charge, CreditEntry, Package, Payment, Settings, Student
from ..services import billing
from ..services.billing import BillingError
from ..utils import month_start, parse_date, parse_money, today_rome
from ._helpers import flash_error, flash_ok, form_int, form_str, get_or_404, uid

bp = Blueprint("payments", __name__)


@bp.route("/payments")
def index():
    today = today_rome()
    first = parse_date(request.args.get("from")) or month_start(today)
    last = parse_date(request.args.get("to")) or today
    student_id = request.args.get("student", type=int)
    method = request.args.get("method") or ""
    show_voided = request.args.get("voided") == "1"

    pq = Payment.query.filter(Payment.paid_on >= first, Payment.paid_on <= last)
    cq = Charge.query.filter(Charge.charged_on >= first, Charge.charged_on <= last)
    if student_id:
        pq = pq.filter(Payment.student_id == student_id)
        cq = cq.filter(Charge.student_id == student_id)
    if method in PAYMENT_METHODS:
        pq = pq.filter(Payment.method == method)
    if not show_voided:
        pq = pq.filter(Payment.voided_at.is_(None))
        cq = cq.filter(Charge.voided_at.is_(None))
    payments = pq.order_by(Payment.paid_on.desc(), Payment.id.desc()).all()
    charges = [] if method else cq.order_by(Charge.charged_on.desc(), Charge.id.desc()).all()

    rows = [("payment", p.paid_on, p) for p in payments] + [("charge", c.charged_on, c) for c in charges]
    rows.sort(key=lambda r: (r[1], r[2].created_at), reverse=True)
    received = sum(p.amount_cents for p in payments if p.voided_at is None)
    charged = sum(c.amount_cents for c in charges if c.voided_at is None)
    by_method = {}
    for p in payments:
        if p.voided_at is None:
            by_method[p.method] = by_method.get(p.method, 0) + p.amount_cents
    students = Student.query.order_by(func.lower(Student.name)).all()
    return render_template(
        "payments/index.html", rows=rows, received=received, charged=charged, by_method=by_method,
        first=first, last=last, student_id=student_id, method=method, show_voided=show_voided,
        students=students, methods=PAYMENT_METHODS, nav="payments",
    )


@bp.route("/students/<int:student_id>/collect", methods=["GET", "POST"])
def collect(student_id):
    settings = Settings.get()
    student = get_or_404(Student, student_id)
    acc = billing.account(student, settings=settings)
    packages = Package.query.filter_by(active=True).order_by(Package.lessons).all()
    price = billing.price_for(student, settings)

    if request.method == "POST":
        mode = form_str("mode") or "lessons"
        package = None
        lessons = 0
        unit_price = None
        sale_amount = None
        if mode == "package":
            package = db.session.get(Package, form_int("package_id", 0))
            if package is None:
                flash_error("err_choose_package")
                return redirect(url_for("payments.collect", student_id=student.id))
        elif mode == "lessons":
            lessons = form_int("lessons", 0, 0, 500) or 0
            unit_price = parse_money(form_str("unit_price"))
            if lessons and unit_price is None:
                flash_error("err_invalid_amount")
                return redirect(url_for("payments.collect", student_id=student.id))
        elif mode != "payment_only":
            mode = "payment_only"
        paid_raw = form_str("amount_paid")
        paid = parse_money(paid_raw) if paid_raw else 0
        if paid is None:
            flash_error("err_invalid_amount")
            return redirect(url_for("payments.collect", student_id=student.id))
        try:
            charge, payment, created = billing.collect(
                student,
                idempotency_key=form_str("idempotency_key", 64),
                lessons=lessons,
                unit_price_cents=unit_price,
                sale_amount_cents=sale_amount,
                package=package,
                description=form_str("description", 200) or None,
                paid_cents=paid,
                method=form_str("method") or "cash",
                paid_on=parse_date(form_str("paid_on")) or today_rome(),
                note=form_str("note", 255) or None,
                user_id=uid(),
            )
        except BillingError as e:
            flash_error("err_" + e.code)
            return redirect(url_for("payments.collect", student_id=student.id))
        flash_ok("msg_payment_saved" if created else "msg_payment_duplicate")
        if payment is not None:
            return redirect(url_for("students.show", student_id=student.id, tab="payments", receipt=payment.id))
        return redirect(url_for("students.show", student_id=student.id, tab="payments"))

    return render_template(
        "payments/collect.html", student=student, acc=acc, packages=packages, price=price,
        methods=PAYMENT_METHODS, nav="payments",
    )


@bp.route("/payments/<int:payment_id>/void", methods=["POST"])
def void_payment(payment_id):
    payment = get_or_404(Payment, payment_id)
    try:
        billing.void_payment(payment, form_str("reason", 255), user_id=uid())
        flash_ok("msg_voided")
    except BillingError as e:
        flash_error("err_" + e.code)
    return redirect(url_for("students.show", student_id=payment.student_id, tab="payments"))


@bp.route("/charges/<int:charge_id>/void", methods=["POST"])
def void_charge(charge_id):
    charge = get_or_404(Charge, charge_id)
    try:
        billing.void_charge(charge, form_str("reason", 255), user_id=uid())
        flash_ok("msg_voided")
    except BillingError as e:
        flash_error("err_" + e.code)
    return redirect(url_for("students.show", student_id=charge.student_id, tab="payments"))


@bp.route("/students/<int:student_id>/adjust", methods=["POST"])
def adjust(student_id):
    student = get_or_404(Student, student_id)
    delta = form_int("delta", 0, -500, 500) or 0
    try:
        billing.adjust_credits(student, delta, form_str("note", 255), user_id=uid())
        flash_ok("msg_saved")
    except BillingError as e:
        flash_error("err_" + e.code)
    return redirect(url_for("students.show", student_id=student.id, tab="payments"))


@bp.route("/credits/<int:entry_id>/reverse", methods=["POST"])
def reverse_credit(entry_id):
    entry = get_or_404(CreditEntry, entry_id)
    try:
        billing.reverse_adjustment(entry, form_str("reason", 255), user_id=uid())
        flash_ok("msg_voided")
    except BillingError as e:
        flash_error("err_" + e.code)
    return redirect(url_for("students.show", student_id=entry.student_id, tab="payments"))
