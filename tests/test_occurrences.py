from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

from icalendar import Calendar

from ncprd_pool.cli import build_feeds
from ncprd_pool.occurrences import expand
from ncprd_pool.parse import Session, Validity
from conftest import TODAY


def _on(exp, d):
    return [o for o in exp.occurrences if o.start.date() == d]


def test_horizon_and_validity_bounds(fall_schedule, cfg):
    exp = expand(fall_schedule, cfg.tz, TODAY, 90)
    assert exp.window_start == TODAY
    assert exp.window_end == date(2026, 12, 31)  # validity end, not today+90
    assert min(o.start.date() for o in exp.occurrences) == TODAY
    assert max(o.start.date() for o in exp.occurrences) <= date(2026, 12, 31)
    short = expand(fall_schedule, cfg.tz, TODAY, 7)
    assert max(o.start.date() for o in short.occurrences) <= TODAY + timedelta(days=6)


def test_closures_have_no_sessions(fall_schedule, cfg):
    exp = expand(fall_schedule, cfg.tz, TODAY, 90)
    for d in (date(2026, 11, 11), date(2026, 11, 26), date(2026, 11, 27), date(2026, 12, 24), date(2026, 12, 25)):
        assert _on(exp, d) == [], d
    assert len(_on(exp, date(2026, 11, 10))) == 7


def test_swim_season_withholds_lap_and_dive_only(fall_schedule, cfg):
    exp = expand(fall_schedule, cfg.tz, TODAY, 90)
    affected = {"lap_13", "lap_all_ages", "dive_well"}
    before = {o.category for o in _on(exp, date(2026, 11, 13))}
    assert affected & before
    for o in exp.occurrences:
        if o.start.date() >= date(2026, 11, 16):
            assert o.category not in affected, o
    nov16 = {o.category for o in _on(exp, date(2026, 11, 16))}
    assert nov16 == {"h2o_fit", "parent_preschool", "deep_h2o"}
    assert exp.withheld["lap_13"] > 0 and exp.withheld["dive_well"] > 0


def test_big_surf_starts_with_its_date_range(fall_schedule, cfg):
    exp = expand(fall_schedule, cfg.tz, date(2026, 10, 3), 90)
    big = [o for o in exp.occurrences if o.category == "big_surf"]
    assert big[0].start.date() == date(2026, 10, 3)


def test_dst_transition_keeps_local_wall_time(fall_schedule, cfg):
    # DST ends 2026-11-01 02:00 PDT. Sessions on both sides start at the same local time.
    exp = expand(fall_schedule, cfg.tz, TODAY, 90)
    sat = next(o for o in _on(exp, date(2026, 10, 31)) if o.category == "big_surf")
    sun = next(o for o in _on(exp, date(2026, 11, 1)) if o.category == "big_surf")
    assert sat.start.time() == sun.start.time() == time(12, 30)
    assert sat.start.utcoffset() == timedelta(hours=-7)
    assert sun.start.utcoffset() == timedelta(hours=-8)
    assert sat.start.astimezone(timezone.utc).hour == 19
    assert sun.start.astimezone(timezone.utc).hour == 20


def test_dst_round_trip_through_ics(fall_schedule, cfg):
    exp = expand(fall_schedule, cfg.tz, TODAY, 90)
    src = {"schedule_url": "https://x/s.pdf", "sha256": "0" * 64}
    feeds = build_feeds(cfg, exp, fall_schedule, src, datetime(2026, 10, 1, tzinfo=timezone.utc))
    cal = Calendar.from_ical(feeds["pool_all.ics"])
    for ev in cal.walk("VEVENT"):
        start = ev.decoded("DTSTART")
        if start.date() == date(2026, 11, 1) and "Big Surf" in str(ev["SUMMARY"]):
            assert start.astimezone(timezone.utc) == datetime(2026, 11, 1, 20, 30, tzinfo=timezone.utc)
            break
    else:
        raise AssertionError("no Big Surf on 2026-11-01")


def test_uids_stable_and_unique(fall_schedule, cfg):
    a = expand(fall_schedule, cfg.tz, TODAY, 90)
    b = expand(fall_schedule, cfg.tz, TODAY + timedelta(days=3), 90)
    assert len({o.uid for o in a.occurrences}) == len(a.occurrences)
    ua = {(o.category, o.start): o.uid for o in a.occurrences}
    shared = [o for o in b.occurrences if (o.category, o.start) in ua]
    assert shared and all(ua[(o.category, o.start)] == o.uid for o in shared)


def test_expired_and_future_schedules(fall_schedule, cfg):
    assert expand(fall_schedule, cfg.tz, date(2027, 1, 5), 90).expired
    future = replace(fall_schedule, validity=Validity(date(2027, 6, 1), date(2027, 8, 31), "x", False))
    exp = expand(future, cfg.tz, TODAY, 90)
    assert exp.not_started and exp.occurrences == []


def test_previous_schedule_covers_dates_before_new_one_starts(fall_schedule, cfg):
    winter = replace(
        fall_schedule,
        sessions=[Session("deep_h2o", 0, time(7), time(8))],
        closures=[], exceptions=[],
        validity=Validity(date(2027, 1, 4), date(2027, 3, 20), "x", False),
    )
    exp = expand(winter, cfg.tz, date(2026, 12, 14), 90, carry_over=fall_schedule)
    dates = sorted({o.start.date() for o in exp.occurrences})
    assert dates[0] == date(2026, 12, 14)  # fall sessions still published for December
    assert all(o.category == "deep_h2o" and o.start.hour == 7 for o in exp.occurrences
               if o.start.date() >= date(2027, 1, 4))
    assert not any(date(2027, 1, 1) <= d < date(2027, 1, 4) for d in dates)
