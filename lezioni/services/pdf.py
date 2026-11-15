"""PDF reports with correct Arabic shaping and right-to-left layout.

fpdf2 + HarfBuzz (uharfbuzz) shape Arabic letters and apply the Unicode
bidi algorithm; the IBM Plex Sans Arabic font (SIL OFL) is embedded and also
covers Latin/Italian text.
"""
import os
from datetime import datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from ..i18n import fmt_date, is_rtl, translate
from ..utils import ROME, format_money

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pdf_fonts")
INK = (30, 41, 59)
MUTED = (100, 116, 139)
LINE = (226, 232, 240)
ACCENT = (47, 111, 79)
SOFT = (241, 245, 249)


class Report(FPDF):
    def __init__(self, lang, settings, title, subtitle=None, orientation="P"):
        super().__init__(orientation=orientation, unit="mm", format="A4")
        self.lang = lang
        self.rtl = is_rtl(lang)
        self.settings = settings
        self.title_text = title
        self.subtitle_text = subtitle
        self.set_margins(15, 15, 15)
        self.set_auto_page_break(True, margin=18)
        self.add_font("plex", "", os.path.join(FONT_DIR, "IBMPlexSansArabic_400Regular.ttf"))
        self.add_font("plex", "B", os.path.join(FONT_DIR, "IBMPlexSansArabic_700Bold.ttf"))
        self.set_text_shaping(use_shaping_engine=True)
        self.set_title(title)
        self.set_creator("Lezioni")
        self.alias_nb_pages()
        self.add_page()

    # ── helpers ──────────────────────────────────────────────────────────────
    def t(self, key, **kw):
        return translate(key, self.lang, **kw)

    def money(self, cents):
        return format_money(cents, self.settings.currency if self.settings else "EUR").replace("\u200e", "")

    def date(self, d, style="medium"):
        return fmt_date(d, lang=self.lang, style=style)

    @property
    def start_align(self):
        return "R" if self.rtl else "L"

    @property
    def end_align(self):
        return "L" if self.rtl else "R"

    def _color(self, rgb, kind="text"):
        getattr(self, f"set_{kind}_color")(*rgb)

    # ── page furniture ───────────────────────────────────────────────────────
    def header(self):
        s = self.settings
        self.set_font("plex", "B", 13)
        self._color(INK)
        name = (s.teacher_name if s and s.teacher_name else self.t("app_name"))
        self.cell(0, 7, name, align=self.start_align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        details = " · ".join(x for x in [s.subject if s else "", s.teacher_phone if s else "",
                                          s.teacher_email if s else ""] if x)
        if details:
            self.set_font("plex", "", 9)
            self._color(MUTED)
            self.cell(0, 5, details, align=self.start_align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)
        self._color(ACCENT, "draw")
        self.set_line_width(0.6)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)
        if self.page_no() == 1:
            self.set_font("plex", "B", 17)
            self._color(INK)
            self.cell(0, 9, self.title_text, align=self.start_align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_font("plex", "", 9)
            self._color(MUTED)
            line = self.t("pdf_generated_on", date=self.date(datetime.now(ROME).date()))
            if self.subtitle_text:
                line = f"{self.subtitle_text} · {line}"
            self.cell(0, 5, line, align=self.start_align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("plex", "", 8)
        self._color(MUTED)
        self.cell(0, 5, f"{self.page_no()} / {{nb}}", align="C")

    # ── content blocks ───────────────────────────────────────────────────────
    def h2(self, text):
        if self.get_y() > self.h - 40:
            self.add_page()
        self.ln(2)
        self.set_font("plex", "B", 12)
        self._color(ACCENT)
        self.cell(0, 7, text, align=self.start_align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self._color(LINE, "draw")
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)

    def para(self, text, size=10, color=INK):
        if not text:
            return
        self.set_font("plex", "", size)
        self._color(color)
        self.multi_cell(0, 5.5, text, align=self.start_align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def kv(self, pairs, label_w=45):
        """Label / value rows."""
        width = self.w - self.l_margin - self.r_margin
        for label, value in pairs:
            if value in (None, ""):
                continue
            y = self.get_y()
            self.set_font("plex", "", 9)
            self._color(MUTED)
            if self.rtl:
                self.set_xy(self.w - self.r_margin - label_w, y)
                self.cell(label_w, 6, str(label), align="R")
                self.set_font("plex", "", 10)
                self._color(INK)
                self.set_xy(self.l_margin, y)
                self.multi_cell(width - label_w - 2, 6, str(value), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            else:
                self.set_xy(self.l_margin, y)
                self.cell(label_w, 6, str(label), align="L")
                self.set_font("plex", "", 10)
                self._color(INK)
                self.set_xy(self.l_margin + label_w + 2, y)
                self.multi_cell(width - label_w - 2, 6, str(value), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def stats(self, items):
        """A row of boxes: [(label, value), ...]."""
        n = len(items)
        gap = 3
        width = self.w - self.l_margin - self.r_margin
        box_w = (width - gap * (n - 1)) / n
        y = self.get_y()
        for i, (label, value) in enumerate(items):
            idx = (n - 1 - i) if self.rtl else i
            x = self.l_margin + idx * (box_w + gap)
            self._color(SOFT, "fill")
            self.rect(x, y, box_w, 18, style="F")
            self.set_xy(x, y + 2)
            self.set_font("plex", "B", 13)
            self._color(INK)
            self.cell(box_w, 7, str(value), align="C")
            self.set_xy(x, y + 10)
            self.set_font("plex", "", 8)
            self._color(MUTED)
            self.cell(box_w, 5, str(label), align="C")
        self.set_xy(self.l_margin, y + 21)

    def table(self, headers, rows, widths, aligns=None, font_size=9, zebra=True):
        """Simple table. ``widths`` are relative weights. Column order is mirrored in RTL."""
        total_w = self.w - self.l_margin - self.r_margin
        scale = total_w / sum(widths)
        widths = [w * scale for w in widths]
        aligns = aligns or [self.start_align] * len(headers)
        order = list(range(len(headers)))
        if self.rtl:
            order.reverse()
        line_h = 6.5

        def draw_header():
            self.set_font("plex", "B", font_size)
            self._color(INK)
            self._color(SOFT, "fill")
            for i in order:
                self.cell(widths[i], line_h + 0.5, str(headers[i]), align=aligns[i], fill=True)
            self.ln(line_h + 0.5)

        draw_header()
        self.set_font("plex", "", font_size)
        for r, row in enumerate(rows):
            if self.get_y() + line_h > self.h - self.b_margin:
                self.add_page()
                draw_header()
                self.set_font("plex", "", font_size)
            if zebra and r % 2 == 1:
                self._color((248, 250, 252), "fill")
                fill = True
            else:
                fill = False
            self._color(INK)
            for i in order:
                text = "" if row[i] is None else str(row[i])
                # keep cells on one line: truncate long values
                while text and self.get_string_width(text) > widths[i] - 2:
                    text = text[:-2] + "…" if len(text) > 2 else ""
                self.cell(widths[i], line_h, text, align=aligns[i], fill=fill)
            self.ln(line_h)
            self._color(LINE, "draw")
            self.set_line_width(0.1)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)

    def signature(self, label):
        self.ln(10)
        self.set_font("plex", "", 9)
        self._color(MUTED)
        w = 60
        x = self.l_margin if self.rtl else self.w - self.r_margin - w
        self._color(LINE, "draw")
        self.line(x, self.get_y() + 8, x + w, self.get_y() + 8)
        self.set_xy(x, self.get_y() + 9)
        self.cell(w, 5, label, align="C")

    def output_bytes(self):
        return bytes(self.output())
