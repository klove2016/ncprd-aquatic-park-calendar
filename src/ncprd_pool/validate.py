"""Pre-publication checks. Any failure here means the last-good feeds stay in place."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar

from .config import Config
from .occurrences import Expansion, Occurrence
from .parse import Schedule


class ValidationError(Exception):
    pass


def _mins(t) -> int:
    return t.hour * 60 + t.minute


def validate_schedule(schedule: Schedule, cfg: Config) -> None:
    v = cfg.validation
    errors = []
    if len(schedule.sessions) < int(v["min_sessions"]):
        errors.append(f"only {len(schedule.sessions)} weekly sessions parsed (< {v['min_sessions']})")
    cats = {s.category for s in schedule.sessions}
    if len(cats) < int(v["min_categories"]):
        errors.append(f"only {len(cats)} session categories parsed (< {v['min_categories']})")
    for s in schedule.sessions:
        dur = _mins(s.end) - _mins(s.start)
        if not int(v["min_session_minutes"]) <= dur <= int(v["max_session_minutes"]):
            errors.append(f"implausible session length {dur} min: {s}")
        if s.start < cfg.earliest_start or s.end > cfg.latest_end:
            errors.append(f"session outside plausible pool hours: {s}")
    by_slot = defaultdict(list)
    for s in schedule.sessions:
        by_slot[(s.category, s.weekday)].append(s)
    for slot, ss in by_slot.items():
        ss.sort(key=lambda s: s.start)
        for a, b in zip(ss, ss[1:]):
            if b.start < a.end and not (a.last_date and b.first_date and a.last_date < b.first_date):
                errors.append(f"overlapping sessions in {slot}: {a.start}-{a.end} and {b.start}-{b.end}")
    val = schedule.validity
    if val.end < val.start:
        errors.append(f"validity ends before it starts: {val}")
    if (val.end - val.start).days > int(v["max_validity_window_days"]):
        errors.append(f"validity window {val.start}..{val.end} is implausibly long")
    if errors:
        raise ValidationError("; ".join(errors))


def validate_expansion(exp: Expansion, schedule: Schedule) -> None:
    if exp.expired:
        raise ValidationError(
            f"linked schedule expired on {schedule.validity.end}; waiting for NCPRD to publish a new one"
        )
    if not exp.occurrences and not exp.not_started:
        raise ValidationError("schedule produced zero sessions in the publishing window")
    uids = [o.uid for o in exp.occurrences]
    if len(uids) != len(set(uids)):
        raise ValidationError("duplicate UIDs generated")


def validate_ics(text: str, expected: list[Occurrence], tz: ZoneInfo) -> None:
    try:
        cal = Calendar.from_ical(text)
    except Exception as e:
        raise ValidationError(f"generated ICS does not parse: {e}") from e
    events = cal.walk("VEVENT")
    if len(events) != len(expected):
        raise ValidationError(f"ICS has {len(events)} events, expected {len(expected)}")
    want = {o.uid: o for o in expected}
    for ev in events:
        uid = str(ev["UID"])
        o = want.get(uid)
        if o is None:
            raise ValidationError(f"unexpected UID {uid} in ICS")
        start, end = ev.decoded("DTSTART"), ev.decoded("DTEND")
        if not isinstance(start, datetime) or start.tzinfo is None:
            raise ValidationError(f"event {uid} has a floating/all-day start")
        # Same instant and same local wall-clock time (catches DST/offset mistakes).
        if start != o.start or end != o.end or start.astimezone(tz).time() != o.start.time():
            raise ValidationError(f"event {uid} time mismatch after round-trip: {start} vs {o.start}")
        if end <= start:
            raise ValidationError(f"event {uid} has non-positive duration")


def count_events_between(ics_text: str, first: date, last: date, tz: ZoneInfo) -> int:
    cal = Calendar.from_ical(ics_text)
    n = 0
    for ev in cal.walk("VEVENT"):
        start = ev.decoded("DTSTART")
        d = start.astimezone(tz).date() if isinstance(start, datetime) else start
        if first <= d <= last:
            n += 1
    return n


def validate_count_change(old_ics: str | None, exp: Expansion, tz: ZoneInfo, max_drop: float) -> str | None:
    """Compare against the published feed over the overlapping dates. Returns a note or raises."""
    if not old_ics:
        return None
    try:
        old = count_events_between(old_ics, exp.window_start, exp.window_end, tz)
    except Exception:
        return "previous feed unreadable; skipped event-count comparison"
    new = len(exp.occurrences)
    if old >= 10 and new < old * (1 - max_drop):
        raise ValidationError(
            f"event count over {exp.window_start}..{exp.window_end} would drop from {old} to {new} "
            f"(> {max_drop:.0%}); re-run with --allow-large-change if this is a real schedule change"
        )
    return f"{old} -> {new} events over {exp.window_start}..{exp.window_end}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)

