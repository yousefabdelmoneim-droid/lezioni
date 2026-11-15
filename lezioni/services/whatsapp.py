"""WhatsApp click-to-chat links (https://wa.me/<number>?text=...).

No message is sent automatically: the link opens WhatsApp with the text
prefilled and the teacher presses send. Automated sending would need the
WhatsApp Business API and Meta approval.
"""
from urllib.parse import quote

from ..i18n import fmt_date, translate
from ..utils import format_money, normalize_phone

KINDS = ("payment", "balance", "absence", "upcoming", "homework")


def recipient(student, settings):
    """Prefer the guardian's phone, fall back to the student's."""
    for raw in (student.guardian_phone, student.phone):
        num = normalize_phone(raw, settings.default_country_code or "39")
        if num:
            return num
    return None


def message(kind, student, settings, account=None, lesson=None, homework=None):
    lang = student.message_language or settings.language or "ar"
    teacher = settings.teacher_name or translate("wa_teacher_fallback", lang)
    params = {"name": student.name, "teacher": teacher}
    if kind in ("payment", "balance") and account is not None:
        params["lessons"] = account.owed_lessons
        params["remaining"] = account.remaining
        params["amount"] = format_money(max(0, account.outstanding_cents), settings.currency)
    if lesson is not None:
        params["date"] = fmt_date(lesson.date, lang=lang, style="day")
        params["time"] = lesson.time
    if homework:
        params["homework"] = homework.strip()

    if kind == "payment":
        if account is not None and account.outstanding_cents > 0:
            key = "wa_payment_amount"
        else:
            key = "wa_payment_lessons"
    elif kind == "balance":
        key = "wa_balance" if account is not None and account.remaining > 0 else "wa_balance_empty"
    elif kind == "absence":
        key = "wa_absence"
    elif kind == "upcoming":
        key = "wa_upcoming"
    elif kind == "homework":
        key = "wa_homework"
    else:
        raise ValueError(kind)
    return translate(key, lang, **params)


def link(number, text):
    if not number:
        return None
    return f"https://wa.me/{number}?text={quote(text)}"
