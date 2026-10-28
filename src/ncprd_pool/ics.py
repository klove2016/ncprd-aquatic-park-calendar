"""Deterministic RFC 5545 writer (byte-identical output for identical input)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

PRODID = "-//ncprd-aquatic-park-calendar//EN"

# US Pacific rules in force since 2007; Google/Apple/Outlook resolve the TZID themselves,
# this block is here for RFC compliance and for clients that need it.
VTIMEZONE_LA = """BEGIN:VTIMEZONE
TZID:America/Los_Angeles
X-LIC-LOCATION:America/Los_Angeles
BEGIN:DAYLIGHT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
TZNAME:PDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
TZNAME:PST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


@dataclass(frozen=True)
class IcsEvent:
    uid: str
    start: datetime  # tz-aware
    end: datetime
    summary: str
    description: str
    location: str
    categories: str
    url: str


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def fold(line: str) -> str:
    """Fold at 75 octets without splitting UTF-8 sequences."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # continuation lines start with a space
        if size + n > limit:
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def _local(dt: datetime, tzid: str) -> str:
    return f";TZID={tzid}:{dt:%Y%m%dT%H%M%S}"


def build_calendar(name: str, description: str, events: list[IcsEvent], dtstamp: datetime, tzid: str) -> str:
    stamp = dtstamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape(name)}",
        f"X-WR-CALDESC:{escape(description)}",
        f"X-WR-TIMEZONE:{tzid}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
        *VTIMEZONE_LA.splitlines(),
    ]
    for ev in sorted(events, key=lambda e: (e.start, e.summary, e.uid)):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev.uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART{_local(ev.start, tzid)}",
            f"DTEND{_local(ev.end, tzid)}",
            f"SUMMARY:{escape(ev.summary)}",
            f"DESCRIPTION:{escape(ev.description)}",
            f"LOCATION:{escape(ev.location)}",
            f"CATEGORIES:{escape(ev.categories)}",
            f"URL:{ev.url}",
            "TRANSP:TRANSPARENT",
            "STATUS:CONFIRMED",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"
