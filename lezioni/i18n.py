"""Tiny dictionary-based i18n: Arabic (default, RTL) and Italian."""
from flask import g, has_request_context, session

from .translations import T

LANGS = ("ar", "it")
RTL = {"ar"}

WEEKDAYS = {
    "ar": ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"],
    "it": ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"],
}
WEEKDAYS_SHORT = {
    "ar": ["اثن", "ثلا", "أرب", "خمي", "جمع", "سبت", "أحد"],
    "it": ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
}
MONTHS = {
    "ar": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"],
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
           "ottobre", "novembre", "dicembre"],
}
MONTHS_SHORT = {
    "ar": MONTHS["ar"],
    "it": ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"],
}


def get_lang():
    if has_request_context():
        lang = getattr(g, "lang", None)
        if lang:
            return lang
        lang = session.get("lang")
        if lang in LANGS:
            return lang
        default = getattr(g, "default_lang", None)
        if default in LANGS:
            return default
    return "ar"


def plural_category(lang, n):
    """CLDR plural category: Arabic zero/one/two/few/many/other, Italian one/other."""
    try:
        n = abs(int(n))
    except (TypeError, ValueError):
        return "other"
    if lang == "ar":
        if n == 0:
            return "zero"
        if n == 1:
            return "one"
        if n == 2:
            return "two"
        rest = n % 100
        if 3 <= rest <= 10:
            return "few"
        if 11 <= rest <= 99:
            return "many"
        return "other"
    if n == 0:
        return "zero"
    return "one" if n == 1 else "other"


def translate(key, lang=None, **kwargs):
    lang = lang or get_lang()
    entry = T.get(key)
    if entry is None:
        text = key
    else:
        text = entry.get(lang) or entry.get("ar") or key
        if isinstance(text, dict):  # plural forms
            count = kwargs.get(text.get("_count", "n"), 0)
            cat = plural_category(lang, count)
            text = text.get(cat) or text.get("other")
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return text


_ = translate


def is_rtl(lang=None):
    return (lang or get_lang()) in RTL


def weekday_name(i, lang=None, short=False):
    table = WEEKDAYS_SHORT if short else WEEKDAYS
    return table[lang or get_lang()][i]


def fmt_date(d, lang=None, style="medium"):
    """medium: 'الأحد 22 نوفمبر 2026' / 'domenica 22 novembre 2026'; short: '22 نوفمبر' / '22 nov'."""
    if d is None:
        return "—"
    lang = lang or get_lang()
    if style == "short":
        return f"{d.day} {MONTHS_SHORT[lang][d.month - 1]}"
    if style == "mon":
        return MONTHS_SHORT[lang][d.month - 1]
    if style == "numeric":
        return d.strftime("%d/%m/%Y")
    if style == "month":
        return f"{MONTHS[lang][d.month - 1]} {d.year}"
    if style == "day":
        return f"{WEEKDAYS[lang][d.weekday()]} {d.day} {MONTHS[lang][d.month - 1]}"
    wd = WEEKDAYS[lang][d.weekday()]
    if lang == "it":
        wd = wd.lower()
    return f"{wd} {d.day} {MONTHS[lang][d.month - 1]} {d.year}"
