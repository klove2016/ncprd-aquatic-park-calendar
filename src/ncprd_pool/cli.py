"""Refresh pipeline: discover -> download -> parse -> validate -> stage -> publish (or keep last-good).

Usage:
  PYTHONPATH=src python -m ncprd_pool                       # live run, writes docs/
  PYTHONPATH=src python -m ncprd_pool --dry-run             # live run, writes nothing
  PYTHONPATH=src python -m ncprd_pool --landing-file x.html --pdf-file y.pdf --today 2026-10-28
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .config import Config, load_config
from .discover import DiscoveryError, choose_schedule_link, find_pdf_links
from .fetch import FetchError, Fetched, Fetcher
from .ics import IcsEvent, build_calendar
from .occurrences import Expansion, expand
from .parse import PARSER_VERSION, ParseError, Schedule, extract_pdf, interpret
from .store import load_json, schedule_from_dict, schedule_to_dict, write_json_atomic, write_text_atomic
from .validate import (ValidationError, utcnow, validate_count_change, validate_expansion,
                       validate_ics, validate_schedule)

PIPELINE_ERRORS = (FetchError, DiscoveryError, ParseError, ValidationError)
ROOT = Path(__file__).resolve().parents[2]
SOURCE_HISTORY_LIMIT = 20


class LocalFetcher:
    """Serves the landing page and schedule from local files (offline runs and tests)."""

    def __init__(self, landing_url: str, landing_file: Path, pdf_file: Path | None):
        self.landing_url, self.landing_file, self.pdf_file = landing_url, landing_file, pdf_file

    def get(self, url: str, accept: str = "*/*") -> Fetched:
        now = datetime.now(timezone.utc)
        if url == self.landing_url:
            return Fetched(url, url, 200, "text/html", self.landing_file.read_bytes(), now)
        if self.pdf_file is None:
            raise FetchError(f"offline mode: no file for {url}")
        return Fetched(url, url, 200, "application/pdf", self.pdf_file.read_bytes(), now)


def acquire(fetcher, cfg: Config) -> tuple[Fetched, str]:
    """Return the schedule document and the anchor text that identified it."""
    landing = fetcher.get(cfg.landing_url, accept="text/html")
    if "html" not in landing.content_type:
        raise DiscoveryError(f"landing page returned {landing.content_type!r}, expected HTML")
    cand = choose_schedule_link(landing.text, landing.url, cfg.discovery)
    doc = fetcher.get(cand.url, accept="application/pdf,*/*;q=0.5")
    if not doc.content.startswith(b"%PDF-") and "html" in doc.content_type:
        # Link went to an intermediate page; follow it one level to the PDF.
        inner = find_pdf_links(doc.text, doc.url, cfg.discovery)
        doc = fetcher.get(inner.url, accept="application/pdf,*/*;q=0.5")
    if not doc.content.startswith(b"%PDF-"):
        raise DiscoveryError(
            f"schedule link {doc.url} did not return a PDF (content-type {doc.content_type!r}, "
            f"{len(doc.content)} bytes); only PDF schedules are supported"
        )
    if len(doc.content) < 10_000:
        raise DiscoveryError(f"schedule PDF is suspiciously small ({len(doc.content)} bytes)")
    return doc, cand.text


def build_feeds(cfg: Config, exp: Expansion, schedule: Schedule, source: dict, dtstamp: datetime) -> dict[str, str]:
    tzid = cfg.tz.key
    by_feed: dict[str, list[IcsEvent]] = {f.key: [] for f in cfg.feeds}
    notes = " ".join(schedule.notes) or "Schedules subject to change."
    for o in exp.occurrences:
        cat = cfg.category(o.category)
        ev = IcsEvent(
            uid=o.uid,
            start=o.start,
            end=o.end,
            summary=f"NCAP {cat.title}",
            description=(
                f"{cat.title} – drop-in session at {cfg.facility_name}.\n"
                f"{notes} Confirm with NCPRD before you go.\n"
                f"Source: {source['schedule_url']} (sha256 {source['sha256'][:12]})"
            ),
            location=cfg.location,
            categories=cat.title,
            url=source["schedule_url"],
        )
        by_feed["all"].append(ev)
        by_feed[cat.feed].append(ev)
    desc = f"Drop-in pool sessions at {cfg.facility_name}, generated from the official NCPRD schedule."
    return {
        f.file: build_calendar(f.name, desc, by_feed[f.key], dtstamp, tzid)
        for f in cfg.feeds
    }


def refresh(cfg: Config, docs: Path, fetcher, now: datetime, today: date,
            allow_large_change: bool = False, dry_run: bool = False) -> tuple[int, dict]:
    status_path = docs / "status.json"
    data_dir = docs / "data"
    prev = load_json(status_path)
    status = {
        "project": "ncprd-aquatic-park-calendar",
        "status": "error",
        "stale": True,
        "last_checked": now.isoformat(),
        "last_success": prev.get("last_success"),
        "last_published": prev.get("last_published"),
        "stale_after_hours": cfg.stale_after_hours,
        "parser_version": PARSER_VERSION,
        "landing_url": cfg.landing_url,
        "source": prev.get("source"),
        "change": None,
        "validity": prev.get("validity"),
        "horizon": prev.get("horizon"),
        "feeds": prev.get("feeds", {}),
        "closures": prev.get("closures", []),
        "exceptions": prev.get("exceptions", []),
        "withheld": prev.get("withheld", {}),
        "warnings": [],
        "errors": [],
        "source_history": prev.get("source_history", []),
        "limitations": [
            "Checked once a day: changes appear only after NCPRD publishes them on ncprd.org.",
            "Sessions in announced exception windows are withheld until official times are published.",
        ],
    }
    exit_code = 1
    try:
        doc, link_text = acquire(fetcher, cfg)
        sha = hashlib.sha256(doc.content).hexdigest()
        old_src = prev.get("source") or {}
        url_changed = bool(old_src) and old_src.get("schedule_url") != doc.url
        content_changed = bool(old_src) and old_src.get("sha256") != sha
        parser_changed = bool(old_src) and old_src.get("parser_version") != PARSER_VERSION
        source = {
            "schedule_url": doc.url,
            "link_text": link_text,
            "sha256": sha,
            "bytes": len(doc.content),
            "content_type": doc.content_type,
            "fetched_at": doc.fetched_at.replace(microsecond=0).isoformat(),
            "first_seen": old_src.get("first_seen") if not content_changed and old_src else now.isoformat(),
            "parser_version": PARSER_VERSION,
        }
        status["attempted_source"] = source
        status["change"] = {
            "url_changed": url_changed,
            "content_changed": content_changed,
            "parser_changed": parser_changed,
            "reparsed": True,
        }

        schedule = interpret(extract_pdf(doc.content), cfg, doc.url, today)
        validate_schedule(schedule, cfg)

        # Previous validated schedule: still authoritative for dates before a newly linked one starts.
        stored = load_json(data_dir / "schedule.json")
        carry = None
        if stored and stored.get("source", {}).get("sha256") != sha:
            carry_dict = stored
        else:
            carry_dict = load_json(data_dir / "previous_schedule.json") or None
        if carry_dict:
            carry = schedule_from_dict(carry_dict)

        exp = expand(schedule, cfg.tz, today, cfg.horizon_days, carry_over=carry)
        validate_expansion(exp, schedule)

        dtstamp = datetime.fromisoformat(source["first_seen"])
        staged = build_feeds(cfg, exp, schedule, source, dtstamp)
        all_file = next(f.file for f in cfg.feeds if f.key == "all")
        for f in cfg.feeds:
            expected = [o for o in exp.occurrences if f.key == "all" or cfg.category(o.category).feed == f.key]
            validate_ics(staged[f.file], expected, cfg.tz)
        old_all = (docs / all_file).read_text() if (docs / all_file).exists() else None
        max_drop = 1.0 if allow_large_change else float(cfg.validation["max_event_drop_ratio"])
        count_note = validate_count_change(old_all, exp, cfg.tz, max_drop)

        # ---- publish (all checks passed)
        changed_files = [name for name, text in staged.items()
                         if not (docs / name).exists() or (docs / name).read_bytes() != text.encode("utf-8")]
        if not dry_run:
            for name in changed_files:
                write_text_atomic(docs / name, staged[name])
            sched_dict = {"source": source, **schedule_to_dict(schedule)}
            if carry is not None and carry_dict is stored:
                write_json_atomic(data_dir / "previous_schedule.json", stored)
            write_json_atomic(data_dir / "schedule.json", sched_dict)
            if changed_files:
                status["last_published"] = now.isoformat()

        warnings = list(schedule.warnings)
        if schedule.validity.uncertain:
            warnings.append(f"Validity window inferred: {schedule.validity.basis}.")
        for e in schedule.exceptions:
            warnings.append(
                f"{e.span.start}..{e.span.end}: {', '.join(e.categories)} withheld – \"{e.text}\""
            )
        if exp.not_started:
            warnings.append(f"Linked schedule starts {schedule.validity.start}, beyond the publishing horizon.")
        if count_note and allow_large_change:
            warnings.append(f"Large event-count change accepted manually: {count_note}")

        history = status["source_history"]
        if not history or (history[0].get("sha256"), history[0].get("schedule_url")) != (sha, doc.url):
            history.insert(0, {"schedule_url": doc.url, "sha256": sha, "first_seen": source["first_seen"]})
            del history[SOURCE_HISTORY_LIMIT:]

        status.update({
            "status": "warning" if warnings else "ok",
            "stale": False,
            "last_success": now.isoformat(),
            "source": source,
            "season": schedule.season,
            "validity": {"start": schedule.validity.start.isoformat(), "end": schedule.validity.end.isoformat(),
                         "basis": schedule.validity.basis, "uncertain": schedule.validity.uncertain},
            "horizon": {"start": exp.window_start.isoformat(), "end": exp.window_end.isoformat(),
                        "days": cfg.horizon_days},
            "carried_over_from_previous_schedule": bool(carry and any(
                o.start.date() < schedule.validity.start for o in exp.occurrences)),
            "feeds": {
                f.key: {"file": f.file, "name": f.name,
                        "events": sum(1 for o in exp.occurrences
                                      if f.key == "all" or cfg.category(o.category).feed == f.key)}
                for f in cfg.feeds
            },
            "closures": [{"start": c.start.isoformat(), "end": c.end.isoformat()} for c in schedule.closures],
            "exceptions": [{"start": e.span.start.isoformat(), "end": e.span.end.isoformat(),
                            "categories": list(e.categories), "note": e.text} for e in schedule.exceptions],
            "withheld": dict(sorted(exp.withheld.items())),
            "event_count_check": count_note,
            "changed_files": changed_files,
            "warnings": warnings,
        })
        status.pop("attempted_source", None)
        exit_code = 0
    except PIPELINE_ERRORS as e:
        status["errors"] = [f"{type(e).__name__}: {e}"]
        status["status"] = "error"
        status["stale"] = True
        status["warnings"] = ["Feeds were NOT updated; the last validated feeds remain published."]
    finally:
        if not dry_run:
            write_json_atomic(status_path, status)
    return exit_code, status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ncprd_pool", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=ROOT / "config" / "config.toml", type=Path)
    ap.add_argument("--docs", default=ROOT / "docs", type=Path)
    ap.add_argument("--landing-file", type=Path, help="use a saved landing page instead of fetching")
    ap.add_argument("--pdf-file", type=Path, help="use a saved schedule PDF instead of fetching")
    ap.add_argument("--today", type=date.fromisoformat, help="override today's date (local)")
    ap.add_argument("--allow-large-change", action="store_true", help="accept a large event-count drop")
    ap.add_argument("--dry-run", action="store_true", help="run every check but write nothing")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    now = utcnow()
    today = args.today or now.astimezone(cfg.tz).date()
    if args.landing_file:
        fetcher = LocalFetcher(cfg.landing_url, args.landing_file, args.pdf_file)
    else:
        fetcher = Fetcher(cfg.http)
    code, status = refresh(cfg, args.docs, fetcher, now, today, args.allow_large_change, args.dry_run)

    print(f"status={status['status']} stale={status['stale']}")
    if status.get("source"):
        print(f"source={status['source']['schedule_url']} sha256={status['source']['sha256'][:16]}")
    for name, f in (status.get("feeds") or {}).items():
        print(f"  {f['file']}: {f['events']} events")
    for w in status["warnings"]:
        print(f"WARNING: {w}")
    for e in status["errors"]:
        print(f"ERROR: {e}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
