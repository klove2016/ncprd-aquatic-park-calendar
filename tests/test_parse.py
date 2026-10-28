from datetime import date, time

import pytest

from ncprd_pool.parse import (DateResolver, ParseError, PdfContent, extract_pdf, interpret, parse_cell,
                              parse_day_list, parse_time_range)
from conftest import FALL_URL, TODAY

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def t(s):
    return time.fromisoformat(s)


# ---------------------------------------------------------------- golden: Fall 2026 official PDF

def _grid(schedule):
    out = {}
    for s in schedule.sessions:
        out.setdefault((s.category, s.weekday), []).append((s.start.strftime("%H:%M"), s.end.strftime("%H:%M")))
    return out


def test_golden_fall_2026_grid(fall_schedule):
    g = _grid(fall_schedule)
    assert len(fall_schedule.sessions) == 50
    assert g[("lap_13", MON)] == [("06:00", "07:55"), ("10:00", "12:30"), ("15:00", "17:55"), ("19:00", "20:00")]
    assert g[("lap_13", TUE)] == [("10:00", "12:30"), ("15:00", "20:00")]
    assert g[("lap_13", FRI)] == [("06:00", "07:55"), ("10:00", "12:30")]
    assert g[("lap_13", SAT)] == [("08:30", "15:30")]  # "8:30AM-\n3:30PM" wrapped across lines
    assert g[("lap_13", SUN)] == [("12:30", "15:30")]
    assert g[("lap_all_ages", SAT)] == [("08:30", "15:30")]
    assert g[("lap_all_ages", SUN)] == [("12:30", "15:30")]
    assert ("lap_all_ages", MON) not in g
    assert g[("dive_well", TUE)] == [("10:00", "12:30"), ("15:00", "17:55"), ("19:00", "20:00")]
    assert ("dive_well", SAT) not in g and ("dive_well", SUN) not in g
    assert g[("deep_h2o", TUE)] == [("18:05", "18:55")]
    assert g[("deep_h2o", SAT)] == [("08:35", "09:25")]
    assert g[("h2o_fit", MON)] == [("08:05", "08:55"), ("18:05", "18:55")]  # en dash in source
    assert g[("h2o_fit", FRI)] == [("08:05", "08:55")]
    assert g[("parent_preschool", SUN)] == [("11:00", "12:00")]  # "11AM-NOON"
    assert g[("parent_preschool", THU)] == [("09:00", "10:00")]
    assert g[("big_surf", SAT)] == [("12:30", "15:30")]
    assert g[("big_surf", SUN)] == [("12:30", "15:30")]
    assert {k[0] for k in g} == {"lap_13", "lap_all_ages", "dive_well", "deep_h2o", "h2o_fit", "big_surf",
                                 "parent_preschool"}


def test_golden_fall_2026_dates(fall_schedule):
    s = fall_schedule
    assert s.season == "fall 2026"
    assert [(c.start, c.end) for c in s.closures] == [
        (date(2026, 8, 24), date(2026, 10, 2)),
        (date(2026, 11, 11), date(2026, 11, 11)),
        (date(2026, 11, 26), date(2026, 11, 27)),
        (date(2026, 12, 24), date(2026, 12, 25)),
        (date(2027, 1, 1), date(2027, 1, 1)),  # yearless 1/1 in a fall guide is next January
    ]
    assert len(s.exceptions) == 1
    exc = s.exceptions[0]
    assert (exc.span.start, exc.span.end) == (date(2026, 11, 16), date(2027, 2, 19))
    assert set(exc.categories) == {"lap_13", "lap_all_ages", "dive_well"}
    assert "High School Swim Season" in exc.text
    assert (s.validity.start, s.validity.end) == (date(2026, 10, 3), date(2026, 12, 31))
    assert s.validity.uncertain  # no explicit validity statement in the PDF
    # Big Surf (Sa/Su 10/3-12/31) is date-restricted; Sunday lap swim at the same time is not.
    big = [x for x in s.sessions if x.category == "big_surf"]
    assert all((x.first_date, x.last_date) == (date(2026, 10, 3), date(2026, 12, 31)) for x in big)
    lap_sun = next(x for x in s.sessions if x.category == "lap_13" and x.weekday == SUN)
    assert lap_sun.first_date is None
    assert any("WINTER BREAK" in w for w in s.warnings)
    assert not any("Unclassified" in w for w in s.warnings)
    assert "Schedules subject to change." in s.notes


def test_does_not_use_facility_hours_or_rates(fall_schedule):
    # Only grid sessions: nothing from fees/membership text, nothing before 6am or after 9pm.
    assert min(s.start for s in fall_schedule.sessions) == t("06:00")
    assert max(s.end for s in fall_schedule.sessions) == t("20:00")


# ---------------------------------------------------------------- time parsing

@pytest.mark.parametrize("s,e,want", [
    ("6", "7:55AM", ("06:00", "07:55")),
    ("12:30", "3:30PM", ("12:30", "15:30")),
    ("10AM", "12:30PM", ("10:00", "12:30")),
    ("11AM", "NOON", ("11:00", "12:00")),
    ("3", "8PM", ("15:00", "20:00")),
    ("10:05", "10:55AM", ("10:05", "10:55")),
    ("11", "1PM", ("11:00", "13:00")),
    ("9:00 a.m.", "noon", ("09:00", "12:00")),
])
def test_parse_time_range(s, e, want):
    a, b = parse_time_range(s, e)
    assert (a.strftime("%H:%M"), b.strftime("%H:%M")) == want


@pytest.mark.parametrize("s,e", [("6", "9"), ("13", "2PM"), ("8PM", "6PM")])
def test_parse_time_range_rejects(s, e):
    with pytest.raises(ParseError):
        parse_time_range(s, e)


def test_parse_cell_multiline_and_wrapped():
    assert len(parse_cell("6-7:55AM\n10AM-12:30PM\n3-5:55PM\n7-8PM")) == 4
    assert parse_cell("8:30AM-\n3:30PM") == [(t("08:30"), t("15:30"))]
    assert parse_cell("") == [] and parse_cell(None) == []


def test_parse_cell_rejects_unknown_text():
    with pytest.raises(ParseError):
        parse_cell("6-7:55AM\nCLOSED for meet")


def test_day_lists():
    assert parse_day_list("Sa/Su") == {SAT, SUN}
    assert parse_day_list("M-F") == {MON, TUE, WED, THU, FRI}
    assert parse_day_list("Fri-Sun") == {FRI, SAT, SUN}


def test_date_resolver_explicit_and_yearless():
    r = DateResolver(date(2026, 10, 15))
    assert r.one("1/1") == date(2027, 1, 1)
    assert r.one("8/24") == date(2026, 8, 24)
    assert r.one("2/19/27") == date(2027, 2, 19)
    assert r.span("12/28-1/3").end == date(2027, 1, 3)


# ---------------------------------------------------------------- structural failures

HEADER = ["", "SUN", "MON", "TUE", "WED", "THUR", "FRI", "SAT"]


def _content(*tables, text="Fall 2026"):
    return PdfContent(tables=list(tables), text=text)


def _row(label, cell="9-10AM"):
    return [label] + [cell] * 7


def test_missing_grid_fails(cfg):
    with pytest.raises(ParseError, match="no weekly schedule grid"):
        interpret(_content([["a", "b"], ["c", "d"]]), cfg, FALL_URL, TODAY)


def test_two_grids_is_ambiguous(cfg):
    g = [HEADER, _row("LAP POOL FITNESS (13 & OLDER)")]
    with pytest.raises(ParseError, match="refusing to guess"):
        interpret(_content(g, g), cfg, FALL_URL, TODAY)


def test_header_missing_a_day_fails(cfg):
    bad = ["", "SUN", "MON", "TUE", "WED", "WED", "FRI", "SAT"]
    with pytest.raises(ParseError, match="each weekday"):
        interpret(_content([bad, _row("DEEP H2O")]), cfg, FALL_URL, TODAY)


def test_row_matching_two_categories_fails(cfg):
    with pytest.raises(ParseError, match="several categories"):
        interpret(_content([HEADER, _row("BIG SURF / DIVE WELL")]), cfg, FALL_URL, TODAY)


def test_unknown_row_is_warned_not_published(cfg):
    s = interpret(_content([HEADER, _row("DEEP H2O"), _row("AQUA ZUMBA")]), cfg, FALL_URL, TODAY)
    assert {x.category for x in s.sessions} == {"deep_h2o"}
    assert any("AQUA ZUMBA" in w for w in s.warnings)


def test_garbled_cell_fails(cfg):
    with pytest.raises(ParseError):
        interpret(_content([HEADER, _row("DEEP H2O", "see front desk")]), cfg, FALL_URL, TODAY)


def test_html_bytes_are_not_a_pdf():
    with pytest.raises(ParseError, match="not a PDF"):
        extract_pdf(b"<!DOCTYPE html><html><body>Error</body></html>")


def test_explicit_validity_is_preferred(cfg):
    s = interpret(_content([HEADER, _row("DEEP H2O")], text="Winter 2027 schedule dates: 1/4-3/20"),
                  cfg, "https://x/winter.pdf", TODAY)
    assert (s.validity.start, s.validity.end) == (date(2027, 1, 4), date(2027, 3, 20))
    assert not s.validity.uncertain


def test_no_dates_uses_conservative_season_window(cfg):
    s = interpret(_content([HEADER, _row("DEEP H2O")], text="Spring 2027"), cfg, "https://x/s.pdf", TODAY)
    assert (s.validity.start, s.validity.end) == (date(2027, 4, 1), date(2027, 6, 30))
    assert s.validity.uncertain


def test_exception_without_named_category_withholds_everything(cfg):
    s = interpret(_content([HEADER, _row("DEEP H2O"), _row("BIG SURF")],
                           text="Fall 2026. *Pool schedule will be adjusted 11/2-11/6 for maintenance."),
                  cfg, FALL_URL, TODAY)
    assert set(s.exceptions[0].categories) == {c.key for c in cfg.categories}


def test_unclassified_dates_are_surfaced(cfg):
    s = interpret(_content([HEADER, _row("DEEP H2O")], text="Fall 2026. Bring a friend on 11/20!"),
                  cfg, FALL_URL, TODAY)
    assert any("Unclassified" in w and "11/20" in w for w in s.warnings)
