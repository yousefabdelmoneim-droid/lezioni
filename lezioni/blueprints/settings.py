from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import AuditLog, Package, Settings
from ..services import audit
from ..utils import format_money, parse_money
from ._helpers import flash_error, flash_ok, form_int, form_str, get_or_404

bp = Blueprint("settings", __name__, url_prefix="/settings")

POLICY_FIELDS = ("charge_present", "charge_late", "charge_absent", "charge_excused", "charge_makeup")


@bp.route("/", methods=["GET", "POST"])
def index():
    s = Settings.get()
    if request.method == "POST":
        s.teacher_name = form_str("teacher_name", 120)
        s.teacher_phone = form_str("teacher_phone", 40)
        s.teacher_email = form_str("teacher_email", 120)
        s.subject = form_str("subject", 120)
        s.address = form_str("address", 255)
        lang = form_str("language")
        s.language = lang if lang in ("ar", "it") else "ar"
        cur = form_str("currency", 3).upper()
        s.currency = cur if cur in ("EUR", "USD", "GBP", "EGP") else "EUR"
        cc = "".join(ch for ch in form_str("default_country_code", 4) if ch.isdigit())
        s.default_country_code = cc or "39"
        raw = form_str("default_price")
        if raw:
            price = parse_money(raw)
            if price is None:
                flash_error("err_invalid_amount")
                return redirect(url_for("settings.index"))
            s.default_price_cents = price
        else:
            s.default_price_cents = None
        s.default_duration_min = form_int("default_duration_min", 60, 15, 600)
        for f in POLICY_FIELDS:
            setattr(s, f, bool(request.form.get(f)))
        s.low_balance_threshold = form_int("low_balance_threshold", 1, 0, 50)
        s.payment_due_days = form_int("payment_due_days", 30, 1, 365)
        s.receipt_footer = form_str("receipt_footer", 1000)
        audit.log("settings.update", "settings", 1, summary="",
                  data={f: getattr(s, f) for f in POLICY_FIELDS})
        db.session.commit()
        flash_ok("msg_saved")
        return redirect(url_for("settings.index"))
    packages = Package.query.order_by(Package.active.desc(), Package.lessons).all()
    return render_template("settings/index.html", s=s, packages=packages, nav="settings")


@bp.route("/packages", methods=["POST"])
def add_package():
    name = form_str("name", 120)
    lessons = form_int("lessons", None, 1, 500)
    price = parse_money(form_str("price"))
    if not name or not lessons or price is None:
        flash_error("err_invalid_package")
        return redirect(url_for("settings.index") + "#packages")
    p = Package(name=name, lessons=lessons, price_cents=price)
    db.session.add(p)
    db.session.flush()
    audit.log("package.create", "package", p.id, summary=f"{name} · {lessons} · {format_money(price, Settings.get().currency)}")
    db.session.commit()
    flash_ok("msg_saved")
    return redirect(url_for("settings.index") + "#packages")


@bp.route("/packages/<int:package_id>/toggle", methods=["POST"])
def toggle_package(package_id):
    p = get_or_404(Package, package_id)
    p.active = not p.active
    audit.log("package.toggle", "package", p.id, summary=p.name)
    db.session.commit()
    flash_ok("msg_saved")
    return redirect(url_for("settings.index") + "#packages")


@bp.route("/password", methods=["POST"])
def change_password():
    current = request.form.get("current") or ""
    new = request.form.get("new") or ""
    confirm = request.form.get("confirm") or ""
    if not current_user.check_password(current):
        flash_error("err_wrong_password")
    elif len(new) < 8:
        flash_error("err_password_short")
    elif new != confirm:
        flash_error("err_password_mismatch")
    else:
        current_user.set_password(new)
        audit.log("auth.password", "user", current_user.id, summary=current_user.username)
        db.session.commit()
        flash_ok("msg_password_changed")
    return redirect(url_for("settings.index") + "#account")


@bp.route("/activity")
def activity():
    page = max(1, request.args.get("page", 1, type=int))
    per = 50
    q = AuditLog.query.order_by(AuditLog.at.desc())
    total = q.count()
    rows = q.offset((page - 1) * per).limit(per).all()
    return render_template("settings/activity.html", rows=rows, page=page, pages=max(1, -(-total // per)),
                           nav="settings")
