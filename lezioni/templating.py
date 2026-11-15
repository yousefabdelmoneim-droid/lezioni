"""Jinja globals, filters and per-request settings."""
from urllib.parse import urlencode

from flask import g, request, url_for

from .i18n import fmt_date, get_lang, is_rtl, translate, weekday_name
from .utils import cents_to_input, format_money, now_rome, signed, today_rome


def current_settings():
    if "settings" not in g:
        from .models import Settings

        try:
            g.settings = Settings.get()
        except Exception:  # table missing during first boot / tests without schema
            g.settings = None
    return g.settings


def register_templating(app):
    @app.before_request
    def _load_lang():
        s = current_settings()
        g.default_lang = s.language if s is not None else "ar"

    app.jinja_env.globals.update(_=translate, url_with=url_with, weekday_name=weekday_name)

    @app.context_processor
    def _globals():
        lang = get_lang()
        s = current_settings()
        return {
            "_": translate,
            "lang": lang,
            "text_dir": "rtl" if is_rtl(lang) else "ltr",
            "settings": s,
            "today": today_rome(),
            "now_time": now_rome().strftime("%H:%M"),
            "weekday_name": weekday_name,
            "url_with": url_with,
        }

    @app.template_filter("money")
    def _money(cents):
        s = current_settings()
        return format_money(cents, s.currency if s else "EUR")

    @app.template_filter("signed")
    def _signed(n, plus=False):
        return signed(n, plus)

    @app.template_filter("money_input")
    def _money_input(cents):
        return cents_to_input(cents)

    @app.template_filter("fdate")
    def _fdate(d, style="medium"):
        return fmt_date(d, style=style)

    @app.template_filter("fdatetime")
    def _fdatetime(dt):
        if dt is None:
            return "—"
        from .models import as_utc
        from .utils import ROME

        local = as_utc(dt).astimezone(ROME)
        return f"{fmt_date(local.date(), style='short')} {local.strftime('%H:%M')}"

    @app.template_filter("nl2br_safe")
    def _nl2br(text):
        from markupsafe import Markup, escape

        if not text:
            return ""
        return Markup("<br>".join(escape(text).split("\n")))


def url_with(**changes):
    """Current URL with some query arguments replaced (None removes)."""
    args = request.args.to_dict()
    for k, v in changes.items():
        if v is None:
            args.pop(k, None)
        else:
            args[k] = v
    base = url_for(request.endpoint, **(request.view_args or {}))
    return base + ("?" + urlencode(args) if args else "")
