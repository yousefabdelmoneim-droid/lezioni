from datetime import timedelta

import pytest

from lezioni.extensions import db
from lezioni.i18n import translate
from lezioni.models import ScheduleSlot, Settings
from lezioni.services import schedule, whatsapp
from lezioni.translations import T
from lezioni.utils import normalize_phone, valid_time

from . import factories as f


@pytest.mark.parametrize("raw,expected", [
    ("+39 333 123 4567", "393331234567"), ("333 1234567", "393331234567"), ("0039 333 1234567", "393331234567"),
    ("+20 100 123 4567", "201001234567"), ("011 1234567", "390111234567"), ("abc", None), ("", None), ("12", None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw,expected", [("9:05", "09:05"), ("18:30", "18:30"), ("24:00", None), ("ab:cd", None),
                                          ("", None), ("7:5", None)])
def test_valid_time(raw, expected):
    assert valid_time(raw) == expected


def test_every_translation_has_both_languages():
    for key, entry in T.items():
        assert entry["ar"].strip() and entry["it"].strip(), key
        # same placeholders in both languages
        import re
        assert set(re.findall(r"{(\w+)}", entry["ar"])) == set(re.findall(r"{(\w+)}", entry["it"])), key


def test_translate_formats_and_falls_back():
    assert translate("n_left", "it", n=3) == "3 rimaste"
    assert translate("unknown_key", "ar") == "unknown_key"


def test_whatsapp_message_language_follows_student(ctx):
    st = Settings.get()
    st.teacher_name = "Prof. Hassan"
    s_it = f.student("Luca", message_language="it", guardian_phone="347 1112223")
    s_ar = f.student("Omar", message_language="ar")
    text_it = whatsapp.message("homework", s_it, st, homework="Esercizi 1-5")
    text_ar = whatsapp.message("homework", s_ar, st, homework="تمارين")
    assert "Compiti di Luca" in text_it and "Prof. Hassan" in text_it
    assert "واجب Omar" in text_ar
    link = whatsapp.link(whatsapp.recipient(s_it, st), text_it)
    assert link.startswith("https://wa.me/393471112223?text=") and " " not in link


def test_recurring_occurrences_and_materialize(ctx, today):
    s = f.student()
    slot = ScheduleSlot(student_id=s.id, weekday=today.weekday(), start_time="16:00", duration_min=60,
                        valid_from=today - timedelta(days=14))
    db.session.add(slot)
    db.session.commit()
    occ = schedule.virtual_occurrences(today - timedelta(days=14), today + timedelta(days=13))
    assert len(occ) == 4
    lesson, created = schedule.materialize(slot, today)
    again, created_again = schedule.materialize(slot, today)
    assert created and not created_again and lesson.id == again.id
    occ = schedule.virtual_occurrences(today - timedelta(days=14), today + timedelta(days=13))
    assert len(occ) == 3  # today's occurrence is now a real lesson


def test_conflicts(ctx, today):
    s = f.student()
    f.lesson(on=today, time="10:00", student=s, duration_min=60)
    assert schedule.conflicts(today, "10:30", 60)
    assert not schedule.conflicts(today, "11:00", 60)
    assert schedule.slot_conflicts(today.weekday(), "10:00", 60) == []


def test_paused_students_slots_are_hidden(ctx, today):
    s = f.student(status="paused")
    db.session.add(ScheduleSlot(student_id=s.id, weekday=today.weekday(), start_time="16:00", valid_from=today))
    db.session.commit()
    assert schedule.virtual_occurrences(today, today) == []
