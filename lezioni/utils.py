import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def now_rome():
    return datetime.now(ROME)


def today_rome():
    return now_rome().date()


def week_start(d):
    return d - timedelta(days=d.weekday())


def month_start(d):
    return d.replace(day=1)


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def valid_time(value):
    """Return a normalized HH:MM string or None."""
    if not value:
        return None
    value = value.strip()
    if len(value) == 4 and value[1] == ":":
        value = "0" + value
    m = _TIME_RE.match(value)
    return f"{m.group(1)}:{m.group(2)}" if m else None


def parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def time_to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def overlaps(start_a, dur_a, start_b, dur_b):
    a0, b0 = time_to_minutes(start_a), time_to_minutes(start_b)
    return a0 < b0 + dur_b and b0 < a0 + dur_a


# ── Money (integer cents, never floats) ─────────────────────────────────────

def parse_money(value):
    """'12,50' / '12.50' / '1.234,56' / '€ 12' -> 1250 cents. Returns None if invalid."""
    if value is None:
        return None
    s = str(value).strip().replace("€", "").replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        # the right-most separator is the decimal one
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if d < 0 or d.as_tuple().exponent < -2 and d != d.quantize(Decimal("0.01")):
        return None
    cents = (d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if cents > 10_000_000_00:  # 10 million – sanity bound
        return None
    return int(cents)


def format_money(cents, currency="EUR"):
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    whole, frac = divmod(cents, 100)
    symbol = {"EUR": "€", "USD": "$", "GBP": "£", "EGP": "E£"}.get(currency, currency + " ")
    whole_s = f"{whole:,}".replace(",", ".")
    # LRM keeps the minus sign on the left inside right-to-left text
    return f"{chr(0x200E) if sign else ''}{sign}{symbol} {whole_s},{frac:02d}"


def signed(n, plus=False):
    """Integer for display in RTL text: LRM keeps '-2' / '+8' readable."""
    if n is None:
        return ""
    s = f"{n:+d}" if plus else str(n)
    return chr(0x200E) + s if (n < 0 or plus) else s


def cents_to_input(cents):
    if cents is None:
        return ""
    return f"{cents // 100},{cents % 100:02d}"


# ── Phones / WhatsApp ───────────────────────────────────────────────────────

def normalize_phone(raw, default_cc="39"):
    """Return digits in international format (no +), or None.

    '+39 333 1234567' -> '393331234567'; '333 1234567' -> '393331234567';
    '0039...' -> '39...'; '+20 10...' -> '2010...'
    """
    if not raw:
        return None
    s = raw.strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if plus:
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif default_cc == "39" and (digits.startswith("3") or digits.startswith("0")) and 9 <= len(digits) <= 11:
        digits = default_cc + digits
    elif not digits.startswith(default_cc) and len(digits) <= 11:
        digits = default_cc + digits.lstrip("0")
    if not 8 <= len(digits) <= 15:
        return None
    return digits


def safe_next(target):
    """Only allow redirects to local paths."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None
