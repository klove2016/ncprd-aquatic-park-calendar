"""Expand a weekly Schedule into concrete, dated, timezone-aware occurrences."""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .parse import Schedule

UID_DOMAIN = "ncprd-aquatic-park-calendar"


@dataclass(frozen=True, order=True)
class Occurrence:
    start: datetime
    end: datetime
    category: str
    uid: str


@dataclass
class Expansion:
    window_start: date
    window_end: date
    occurrences: list[Occurrence]
    closed_days: list[date] = field(default_factory=list)
    withheld: Counter = field(default_factory=Counter)  # category -> count
    expired: bool = False
    not_started: bool = False


def make_uid(category: str, start: datetime, end: datetime) -> str:
    # Wall-clock local times: the same session keeps its UID across reparses and source URL changes.
    key = f"{category}|{start:%Y-%m-%dT%H:%M}|{end:%H:%M}"
    return hashlib.sha256(key.encode()).hexdigest()[:32] + "@" + UID_DOMAIN


def expand(schedule: Schedule, tz: ZoneInfo, today: date, horizon_days: int,
           carry_over: Schedule | None = None) -> Expansion:
    """Expand `schedule` over [today, today+horizon) clipped to its validity.

    `carry_over` is the previously published schedule: when a new season's PDF is linked
    before the old season ends, the old one still covers the dates before the new one starts.
    """
    v = schedule.validity
    horizon_end = today + timedelta(days=horizon_days - 1)
    exp = _expand_window(schedule, tz, max(today, v.start), min(horizon_end, v.end))
    exp.expired = today > v.end
    exp.not_started = horizon_end < v.start
    if carry_over is not None and carry_over.validity.start < v.start:
        old = _expand_window(carry_over, tz, max(today, carry_over.validity.start),
                             min(horizon_end, carry_over.validity.end, v.start - timedelta(days=1)))
        if old.occurrences:
            exp.window_start = min(exp.window_start, old.window_start)
            exp.occurrences = sorted(set(exp.occurrences) | set(old.occurrences))
            exp.closed_days = sorted(set(exp.closed_days) | set(old.closed_days))
            exp.withheld.update(old.withheld)
    return exp


def _expand_window(schedule: Schedule, tz: ZoneInfo, window_start: date, window_end: date) -> Expansion:
    exp = Expansion(window_start, window_end, [])
    seen = set()
    d = window_start
    while d <= window_end:
        if any(c.contains(d) for c in schedule.closures):
            exp.closed_days.append(d)
            d += timedelta(days=1)
            continue
        for s in schedule.sessions:
            if s.weekday != d.weekday():
                continue
            if (s.first_date and d < s.first_date) or (s.last_date and d > s.last_date):
                continue
            if any(e.span.contains(d) and s.category in e.categories for e in schedule.exceptions):
                exp.withheld[s.category] += 1
                continue
            start = datetime.combine(d, s.start, tzinfo=tz)
            end = datetime.combine(d, s.end, tzinfo=tz)
            key = (s.category, start, end)
            if key in seen:
                continue
            seen.add(key)
            exp.occurrences.append(Occurrence(start, end, s.category, make_uid(s.category, start, end)))
        d += timedelta(days=1)
    exp.occurrences.sort()
    return exp
