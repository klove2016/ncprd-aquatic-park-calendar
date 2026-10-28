from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar

from ncprd_pool.ics import IcsEvent, build_calendar, escape, fold

LA = ZoneInfo("America/Los_Angeles")


def _ev(**kw):
    base = dict(uid="u1@x", start=datetime(2026, 11, 1, 12, 30, tzinfo=LA), end=datetime(2026, 11, 1, 15, 30, tzinfo=LA),
                summary="Big Surf; fun, waves", description="line1\nline2 " + "é" * 80, location="A, B",
                categories="Rec", url="https://example.com/a.pdf")
    base.update(kw)
    return IcsEvent(**base)


def test_lines_are_folded_to_75_octets_and_crlf():
    text = build_calendar("Cal", "Desc", [_ev()], datetime(2026, 1, 1, tzinfo=timezone.utc), "America/Los_Angeles")
    assert text.endswith("\r\n")
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


def test_escaping_round_trips():
    assert escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    text = build_calendar("Cal", "Desc", [_ev()], datetime(2026, 1, 1, tzinfo=timezone.utc), "America/Los_Angeles")
    ev = Calendar.from_ical(text).walk("VEVENT")[0]
    assert str(ev["SUMMARY"]) == "Big Surf; fun, waves"
    assert str(ev["DESCRIPTION"]).startswith("line1\nline2 é")


def test_output_is_deterministic_regardless_of_input_order():
    a, b = _ev(), _ev(uid="u2@x", start=datetime(2026, 11, 2, 9, tzinfo=LA), end=datetime(2026, 11, 2, 10, tzinfo=LA))
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert build_calendar("C", "D", [a, b], stamp, "America/Los_Angeles") == \
        build_calendar("C", "D", [b, a], stamp, "America/Los_Angeles")


def test_fold_does_not_split_multibyte():
    folded = fold("X:" + "é" * 100)
    for part in folded.split("\r\n "):
        part.encode("utf-8").decode("utf-8")
