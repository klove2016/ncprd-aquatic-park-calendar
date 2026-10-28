"""Parse an NCAP drop-in schedule PDF into a structured, validated Schedule.

Two layers:
  * extract_pdf()  - PDF bytes -> PdfContent (raw table rows + page text)
  * interpret()    - PdfContent -> Schedule (sessions, closures, exceptions, validity)

Anything that cannot be interpreted with confidence raises ParseError (for the grid)
or becomes a warning / withheld window (for dated notes). Nothing is guessed silently.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta

from .config import Category, Config

PARSER_VERSION = "1.0.0"


class ParseError(Exception):
    pass


# ---------------------------------------------------------------- data model

@dataclass(frozen=True)
class Session:
    category: str
    weekday: int  # Monday=0 .. Sunday=6
    start: time
    end: time
    first_date: date | None = None  # optional per-session date restriction
    last_date: date | None = None


@dataclass(frozen=True)
class DateSpan:
    start: date
    end: date
    text: str

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


@dataclass(frozen=True)
class Exception_:
    span: DateSpan
    categories: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class Validity:
    start: date
    end: date
    basis: str
    uncertain: bool


@dataclass
class Schedule:
    season: str | None
    sessions: list[Session]
    closures: list[DateSpan]
    exceptions: list[Exception_]
    validity: Validity
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PdfContent:
    tables: list[list[list[str | None]]]
    text: str


# ---------------------------------------------------------------- PDF layer

def extract_pdf(pdf_bytes: bytes) -> PdfContent:
    if not pdf_bytes.startswith(b"%PDF-"):
        head = pdf_bytes[:200].decode("latin-1", errors="replace")
        raise ParseError(f"downloaded schedule is not a PDF (starts with {head[:60]!r})")
    import pdfplumber  # imported lazily: heavy, and not needed by unit tests of interpret()

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            tables, texts = [], []
            for page in pdf.pages:
                tables.extend(page.extract_tables())
                texts.append(page.extract_text() or "")
    except Exception as e:  # pdfminer raises a zoo of exception types
        raise ParseError(f"could not read PDF: {e}") from e
    return PdfContent(tables=tables, text="\n".join(texts))


# ---------------------------------------------------------------- times

_TIME = r"(?:noon|midnight|\d{1,2}(?::\d{2})?\s*(?:[ap]\.?\s?m\.?)?)"
_RANGE_RX = re.compile(rf"(?P<s>{_TIME})\s*[-–—]\s*(?P<e>{_TIME})", re.I)
_TOKEN_RX = re.compile(r"^(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<mer>[ap])?", re.I)


def _token(tok: str) -> tuple[int, int, str | None]:
    tok = tok.strip().lower()
    if tok == "noon":
        return 12, 0, "p"
    if tok == "midnight":
        return 12, 0, "a"
    m = _TOKEN_RX.match(tok)
    if not m:
        raise ParseError(f"bad time token {tok!r}")
    h, mi = int(m["h"]), int(m["m"] or 0)
    if not (1 <= h <= 12 and 0 <= mi < 60):
        raise ParseError(f"bad time token {tok!r}")
    return h, mi, (m["mer"] or "").lower() or None


def _minutes(h: int, mi: int, mer: str) -> int:
    return (h % 12 + (12 if mer == "p" else 0)) * 60 + mi


def parse_time_range(s_tok: str, e_tok: str) -> tuple[time, time]:
    """'6' '7:55AM' -> 06:00-07:55; '12:30' '3:30PM' -> 12:30-15:30; '11AM' 'NOON' -> 11:00-12:00."""
    sh, sm, smer = _token(s_tok)
    eh, em, emer = _token(e_tok)
    if emer is None and smer is None:
        raise ParseError(f"time range {s_tok}-{e_tok} has no AM/PM")
    if emer is None:
        emer = smer
        if _minutes(eh, em, emer) <= _minutes(sh, sm, smer):
            emer = "p" if smer == "a" else "a"
    if smer is None:
        smer = emer
        if _minutes(sh, sm, smer) >= _minutes(eh, em, emer):
            smer = "a" if emer == "p" else "p"
    start, end = _minutes(sh, sm, smer), _minutes(eh, em, emer)
    if end <= start:
        raise ParseError(f"time range {s_tok}-{e_tok} ends before it starts")
    return time(start // 60, start % 60), time(end // 60, end % 60)


def parse_cell(cell: str | None) -> list[tuple[time, time]]:
    if not cell or not cell.strip():
        return []
    # Re-join ranges wrapped across lines, e.g. "8:30AM-\n3:30PM".
    text = re.sub(r"[-–—]\s*\n\s*", "-", cell.strip())
    out = []
    for line in text.splitlines():
        rest = line
        for m in _RANGE_RX.finditer(line):
            out.append(parse_time_range(m["s"], m["e"]))
            rest = rest.replace(m.group(0), " ", 1)
        if not re.fullmatch(r"[\s,;&/*]*", rest):
            raise ParseError(f"unparseable text {rest.strip()!r} in cell {cell!r}")
    return out


# ---------------------------------------------------------------- dates

_DATE = r"\d{1,2}/\d{1,2}(?:/\d{2,4})?"
_DRANGE = rf"{_DATE}(?:\s*[-–—]\s*{_DATE})?"
_DLIST = rf"{_DRANGE}(?:\s*,?\s*(?:and\s+)?{_DRANGE})*"
_DATE_RX = re.compile(_DATE)
_DRANGE_RX = re.compile(_DRANGE)

SEASONS = {
    # anchor (month, day) used to place yearless dates; default validity window.
    "winter": {"anchor": (2, 1), "start": (1, 1), "end": (3, 31)},
    "spring": {"anchor": (5, 1), "start": (4, 1), "end": (6, 30)},
    "summer": {"anchor": (7, 15), "start": (6, 15), "end": (8, 31)},
    "fall": {"anchor": (10, 15), "start": (9, 1), "end": (12, 31)},
}


class DateResolver:
    """Resolve 'm/d' (year implied by the season) and 'm/d/yy' tokens to dates."""

    def __init__(self, anchor: date):
        self.anchor = anchor

    def one(self, tok: str) -> date:
        parts = [int(p) for p in tok.split("/")]
        if len(parts) == 3:
            y = parts[2] + 2000 if parts[2] < 100 else parts[2]
            return date(y, parts[0], parts[1])
        options = []
        for y in (self.anchor.year - 1, self.anchor.year, self.anchor.year + 1):
            try:
                options.append(date(y, parts[0], parts[1]))
            except ValueError:
                pass
        if not options:
            raise ParseError(f"invalid date {tok!r}")
        return min(options, key=lambda d: abs((d - self.anchor).days))

    def span(self, text: str) -> DateSpan:
        toks = _DATE_RX.findall(text)
        start = self.one(toks[0])
        end = self.one(toks[1]) if len(toks) > 1 else start
        if end < start and len(toks[1].split("/")) == 2:
            end = date(end.year + 1, end.month, end.day)
        if end < start:
            raise ParseError(f"date range {text!r} ends before it starts")
        return DateSpan(start, end, text.strip())

    def spans(self, text: str) -> list[DateSpan]:
        return [self.span(m.group(0)) for m in _DRANGE_RX.finditer(text)]


# ---------------------------------------------------------------- days

_DAY_NAMES = {"sun": 6, "su": 6, "mon": 0, "mo": 0, "m": 0, "tue": 1, "tu": 1, "wed": 2, "we": 2,
              "w": 2, "thu": 3, "th": 3, "fri": 4, "fr": 4, "f": 4, "sat": 5, "sa": 5}
_DAY_RX = re.compile(r"^(sun|mon|tues?|wed|thur?s?|fri|sat)(?:day|nesday|urday|sday)?\.?$", re.I)


def header_weekday(cell: str | None) -> int | None:
    m = _DAY_RX.match((cell or "").strip())
    return _DAY_NAMES[m.group(1).lower()[:3]] if m else None


def parse_day_list(text: str) -> set[int]:
    """'Sa/Su' -> {5, 6}; 'M-F' -> {0..4}."""
    days: set[int] = set()
    for part in re.split(r"[/,&]|\band\b", text.lower()):
        ends = [p.strip(" .") for p in re.split(r"[-–]", part) if p.strip(" .")]
        keys = []
        for e in ends:
            k = e[:3] if e[:3] in _DAY_NAMES else e[:2] if e[:2] in _DAY_NAMES else e[:1]
            if k not in _DAY_NAMES:
                raise ParseError(f"unknown day {e!r} in {text!r}")
            keys.append(_DAY_NAMES[k])
        if len(keys) == 1:
            days.add(keys[0])
        elif len(keys) == 2:
            d = keys[0]
            while True:
                days.add(d)
                if d == keys[1]:
                    break
                d = (d + 1) % 7
    return days


# ---------------------------------------------------------------- grid

def _norm(s: str | None) -> str:
    return " ".join((s or "").split())


def find_grid(tables: list[list[list[str | None]]]) -> tuple[list[list[str | None]], dict[int, int]]:
    grids = []
    for t in tables:
        if not t:
            continue
        cols = {i: header_weekday(c) for i, c in enumerate(t[0])}
        cols = {i: d for i, d in cols.items() if d is not None}
        if len(cols) >= 5:
            grids.append((t, cols))
    if not grids:
        raise ParseError("no weekly schedule grid (a table with a day-of-week header) found in PDF")
    if len(grids) > 1:
        raise ParseError(f"found {len(grids)} day-of-week grids in PDF; refusing to guess which is current")
    table, cols = grids[0]
    if sorted(cols.values()) != list(range(7)):
        raise ParseError(f"grid header does not contain each weekday exactly once: {table[0]}")
    return table, cols


def parse_grid(content: PdfContent, categories: tuple[Category, ...]) -> tuple[list[Session], list[str]]:
    table, cols = find_grid(content.tables)
    sessions, warnings = [], []
    for row in table[1:]:
        label = _norm(row[0] if row else "")
        cells = {wd: row[i] if i < len(row) else None for i, wd in cols.items()}
        has_times = any(_RANGE_RX.search(c or "") for c in cells.values())
        matches = [c for c in categories if c.row_pattern.search(label)]
        if len(matches) > 1:
            raise ParseError(f"row {label!r} matches several categories: {[c.key for c in matches]}")
        if not matches:
            if has_times:
                warnings.append(f"Unrecognised schedule row {label!r} was not published.")
            continue
        cat = matches[0]
        for wd, cell in cells.items():
            for start, end in parse_cell(cell):
                sessions.append(Session(cat.key, wd, start, end))
    return sorted(set(sessions), key=lambda s: (s.weekday, s.start, s.category)), warnings


# ---------------------------------------------------------------- notes / dated text

_ADJUST_RX = re.compile(r"adjust|modif|alter|reduc|limit|affect|chang|cancel|closed|unavailable|suspend", re.I)
_CLOSURE_RX = re.compile(rf"closures?\s*:?\s*(?P<list>{_DLIST})", re.I)
_TIMEDATE_RX = re.compile(
    rf"time\s*/\s*date\s*:?\s*(?P<s>{_TIME})\s*[-–—]\s*(?P<e>{_TIME})\s+(?P<days>[A-Za-z/,&\- ]+?)\s+(?P<range>{_DRANGE})",
    re.I,
)
_SPECIAL_RX = re.compile(rf"\b(?P<label>[A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+){{0,4}})\s*:\s*(?P<list>{_DLIST})")
_EXPLICIT_VALIDITY_RX = re.compile(
    rf"(?:valid|effective|schedule\s+dates?|session\s+dates?)\s*:?\s*(?P<range>{_DATE}\s*[-–—]\s*{_DATE})", re.I
)
_SEASON_RX = re.compile(r"(fall|winter|spring|summer)[\s_-]*(20\d\d)", re.I)


def detect_season(text: str, source_url: str) -> tuple[str | None, int | None]:
    for hay in (source_url, text):
        m = _SEASON_RX.search(hay or "")
        if m:
            return m.group(1).lower(), int(m.group(2))
    return None, None


def _categories_mentioned(text: str, categories: tuple[Category, ...]) -> tuple[str, ...]:
    low = text.lower()
    hit = tuple(c.key for c in categories if any(k in low for k in c.keywords))
    return hit or tuple(c.key for c in categories)  # unknown scope -> assume everything is affected


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("* ", 0, start), text.rfind("*", 0, start))
    right = text.find(". ", end)
    return text[left + 1 if left >= 0 else 0: right + 1 if right >= 0 else len(text)].strip(" *")


def interpret(content: PdfContent, cfg: Config, source_url: str, reference_date: date) -> Schedule:
    sessions, warnings = parse_grid(content, cfg.categories)
    notes: list[str] = []
    flat = " ".join(content.text.split())

    season, year = detect_season(flat, source_url)
    if season:
        am, ad = SEASONS[season]["anchor"]
        resolver = DateResolver(date(year, am, ad))
    else:
        resolver = DateResolver(reference_date)
        warnings.append("Could not identify the schedule season/year; yearless dates were placed near today.")

    consumed: list[tuple[int, int]] = []

    def take(m: re.Match) -> None:
        consumed.append((m.start(), m.end()))

    def is_consumed(a: int, b: int) -> bool:
        return any(s <= a and b <= e for s, e in consumed)

    # Facility closures.
    closures: list[DateSpan] = []
    for m in _CLOSURE_RX.finditer(flat):
        closures.extend(resolver.spans(m["list"]))
        take(m)

    # "Time/Date: 12:30-3:30pm Sa/Su 10/3-12/31" restricts matching grid sessions to a date range.
    restricted: dict[Session, DateSpan] = {}
    range_spans: list[DateSpan] = []
    for m in _TIMEDATE_RX.finditer(flat):
        take(m)
        start, end = parse_time_range(m["s"], m["e"])
        try:
            days = parse_day_list(m["days"])
        except ParseError as e:
            warnings.append(f"Could not read days in {m.group(0)!r}: {e}")
            continue
        span = resolver.span(m["range"])
        range_spans.append(span)
        same_time = [s for s in sessions if s.start == start and s.end == end]
        # The note describes one program: only categories whose sessions at this time fall on
        # exactly the listed days (e.g. Big Surf Sa/Su, not Sunday-only lap swim at 12:30).
        hits = [s for s in same_time if {x.weekday for x in same_time if x.category == s.category} == days]
        if not hits:
            warnings.append(f"Dated note {m.group(0)!r} matches no grid session.")
        for s in hits:
            restricted[s] = span
    sessions = [
        Session(s.category, s.weekday, s.start, s.end, restricted[s].start, restricted[s].end) if s in restricted else s
        for s in sessions
    ]

    # Explicit validity statement, if the document has one.
    explicit = _EXPLICIT_VALIDITY_RX.search(flat)
    if explicit:
        take(explicit)

    # Exception windows: sentences that pair a date range with adjust/closed/cancel wording.
    exceptions: list[Exception_] = []
    for m in _DRANGE_RX.finditer(flat):
        if is_consumed(m.start(), m.end()):
            continue
        sentence = _sentence_around(flat, m.start(), m.end())
        if _ADJUST_RX.search(sentence):
            span = resolver.span(m.group(0))
            cats = _categories_mentioned(sentence, cfg.categories)
            exceptions.append(Exception_(span, cats, sentence))
            consumed.append((m.start(), m.end()))

    # Labelled special periods ("WINTER BREAK: 12/21-12/23, 12/28-12/31") - times not given in the grid.
    for m in _SPECIAL_RX.finditer(flat):
        if is_consumed(m.start("list"), m.end("list")):
            continue
        take(m)
        warnings.append(
            f"Special period '{m['label']}: {m['list']}' has no verifiable session times in the grid; "
            "any extra sessions on those dates are not published. Regular grid sessions are still shown."
        )

    for m in _DATE_RX.finditer(flat):
        if not is_consumed(m.start(), m.end()):
            warnings.append(f"Unclassified dated text near {flat[max(0, m.start() - 60): m.end() + 40]!r}")

    if re.search(r"subject to change", flat, re.I):
        notes.append("Schedules subject to change.")

    validity = _validity(explicit, resolver, season, year, closures, range_spans, cfg, reference_date)
    return Schedule(season=f"{season} {year}" if season else None, sessions=sessions, closures=closures,
                    exceptions=exceptions, validity=validity, notes=notes, warnings=warnings)


def _validity(explicit, resolver, season, year, closures, range_spans, cfg, reference_date) -> Validity:
    if explicit:
        span = resolver.span(explicit["range"])
        return Validity(span.start, span.end, f"stated in schedule: {explicit.group(0)!r}", uncertain=False)

    if range_spans:
        start = min(s.start for s in range_spans)
        basis = [f"start {start} from dated session range"]
    else:
        long_closures = [c for c in closures if (c.end - c.start).days >= 13]
        if long_closures:
            start = max(c.end for c in long_closures) + timedelta(days=1)
            basis = [f"start {start} = reopening after facility closure"]
        elif season:
            sm, sd = SEASONS[season]["start"]
            start = date(year, sm, sd)
            basis = [f"start {start} = conventional {season} season start"]
        else:
            start = reference_date
            basis = [f"start {start} = first seen (no dates in schedule)"]

    if range_spans:
        end = max(s.end for s in range_spans)
        basis.append(f"end {end} from latest dated session range")
    elif season:
        em, ed = SEASONS[season]["end"]
        end = date(year, em, ed)
        basis.append(f"end {end} = conventional {season} season end")
    else:
        end = start + timedelta(days=cfg.max_validity_days)
        basis.append("end = conservative limit")
    cap = start + timedelta(days=cfg.max_validity_days)
    if end > cap:
        end = cap
        basis.append(f"capped at {cfg.max_validity_days} days")
    return Validity(start, end, "; ".join(basis) + " (schedule states no explicit validity window)", uncertain=True)
