import time
from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..i18n import LANGS, _
from ..models import User, as_utc, utcnow
from ..services import audit
from ..utils import safe_next

bp = Blueprint("auth", __name__)

MAX_FAILS = 5
LOCK_MINUTES = 15
_ip_attempts = {}  # ip -> [timestamps]; single worker, resets on restart (accounts also lock in DB)


def _ip_blocked(ip):
    now = time.time()
    attempts = [t for t in _ip_attempts.get(ip, []) if now - t < LOCK_MINUTES * 60]
    _ip_attempts[ip] = attempts
    return len(attempts) >= 20


def _ip_fail(ip):
    _ip_attempts.setdefault(ip, []).append(time.time())


def authenticate(username, password, ip):
    """Check credentials with IP and account lockout. Returns (user, None) or (None, "locked"|"invalid").

    Shared by the web login form and the mobile API.
    """
    username = (username or "").strip().lower()
    password = password or ""
    if _ip_blocked(ip):
        return None, "locked"
    user = User.query.filter_by(username=username).first()
    now = utcnow()
    if user and user.locked_until and as_utc(user.locked_until) > now:
        return None, "locked"
    if user and user.check_password(password):
        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = now
        db.session.commit()
        return user, None
    _ip_fail(ip)
    if user:
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= MAX_FAILS:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
            audit.log("auth.locked", "user", user.id, user_id=user.id, summary=user.username)
        db.session.commit()
    return None, "invalid"


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    no_users = User.query.count() == 0
    if request.method == "POST" and not no_users:
        ip = request.remote_addr or ""  # real client IP (ProxyFix in production)
        user, error = authenticate(request.form.get("username"), request.form.get("password"), ip)
        if user is not None:
            session.permanent = True
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(safe_next(request.args.get("next")) or url_for("dashboard.index"))
        if error == "locked":
            flash(_("login_locked"), "error")
            return render_template("auth/login.html", no_users=no_users), 429
        flash(_("login_invalid"), "error")
        return render_template("auth/login.html", no_users=no_users), 401
    return render_template("auth/login.html", no_users=no_users)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.pop("lang", None)
    return redirect(url_for("auth.login"))


@bp.route("/lang/<code>", methods=["POST"])
def set_lang(code):
    if code in LANGS:
        session["lang"] = code
    return redirect(safe_next(request.form.get("next")) or url_for("dashboard.index"))
