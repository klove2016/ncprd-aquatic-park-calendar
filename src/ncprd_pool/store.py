"""JSON (de)serialisation of validated schedules and atomic file writes."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, time
from pathlib import Path

from .parse import DateSpan, Exception_, Schedule, Session, Validity


def schedule_to_dict(s: Schedule) -> dict:
    return {
        "season": s.season,
        "validity": {"start": s.validity.start.isoformat(), "end": s.validity.end.isoformat(),
                     "basis": s.validity.basis, "uncertain": s.validity.uncertain},
        "sessions": [
            {"category": x.category, "weekday": x.weekday, "start": x.start.strftime("%H:%M"),
             "end": x.end.strftime("%H:%M"),
             "first_date": x.first_date.isoformat() if x.first_date else None,
             "last_date": x.last_date.isoformat() if x.last_date else None}
            for x in s.sessions
        ],
        "closures": [_span(c) for c in s.closures],
        "exceptions": [{**_span(e.span), "categories": list(e.categories), "note": e.text} for e in s.exceptions],
        "notes": s.notes,
        "warnings": s.warnings,
    }


def _span(c: DateSpan) -> dict:
    return {"start": c.start.isoformat(), "end": c.end.isoformat(), "text": c.text}


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def schedule_from_dict(d: dict) -> Schedule:
    v = d["validity"]
    return Schedule(
        season=d["season"],
        validity=Validity(_d(v["start"]), _d(v["end"]), v["basis"], v["uncertain"]),
        sessions=[Session(x["category"], x["weekday"], time.fromisoformat(x["start"]), time.fromisoformat(x["end"]),
                          _d(x["first_date"]), _d(x["last_date"])) for x in d["sessions"]],
        closures=[DateSpan(_d(c["start"]), _d(c["end"]), c["text"]) for c in d["closures"]],
        exceptions=[Exception_(DateSpan(_d(e["start"]), _d(e["end"]), e["text"]), tuple(e["categories"]), e["note"])
                    for e in d["exceptions"]],
        notes=d.get("notes", []),
        warnings=d.get("warnings", []),
    )


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def write_json_atomic(path: Path, obj: dict) -> None:
    write_text_atomic(path, json.dumps(obj, indent=2, sort_keys=False) + "\n")
