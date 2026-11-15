from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

from ..extensions import db
from ..i18n import _
from ..utils import safe_next


def form_str(name, max_len=255, default=""):
    value = (request.form.get(name) or "").strip()
    return value[:max_len] if value else default


def form_int(name, default=None, lo=None, hi=None):
    raw = (request.form.get(name) or "").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if lo is not None and value < lo:
        return default
    if hi is not None and value > hi:
        return default
    return value


def get_or_404(model, ident):
    obj = db.session.get(model, ident)
    if obj is None:
        abort(404)
    return obj


def uid():
    return current_user.id if current_user.is_authenticated else None


def back(default_endpoint, **kwargs):
    return redirect(safe_next(request.form.get("next")) or safe_next(request.args.get("next"))
                    or url_for(default_endpoint, **kwargs))


def flash_error(key, **params):
    flash(_(key, **params), "error")


def flash_ok(key, **params):
    flash(_(key, **params), "success")
