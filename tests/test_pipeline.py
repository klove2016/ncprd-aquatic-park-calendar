"""End-to-end acceptance tests of the refresh pipeline (no network)."""
import json
from datetime import date, timedelta

import pytest

from ncprd_pool import cli
from ncprd_pool.parse import PdfContent
from conftest import FALL_URL, NOW, ROOT, TODAY

FEEDS = ["pool_all.ics", "pool_lap.ics", "pool_dive.ics", "pool_h2o.ics", "pool_recreation.ics", "pool_preschool.ics"]


def snapshot(docs):
    return {f: (docs / f).read_text() for f in FEEDS}


def status(docs):
    return json.loads((docs / "status.json").read_text())


@pytest.fixture
def published(tmp_path, cfg, make_fetcher):
    code, _ = cli.refresh(cfg, tmp_path, make_fetcher(), NOW, TODAY)
    assert code == 0
    return tmp_path


def assert_last_good_kept(docs, before, st):
    assert snapshot(docs) == before
    assert st["status"] == "error" and st["errors"]
    assert st["source"]["schedule_url"] == FALL_URL  # still describes the published (last-good) source


def test_first_run_publishes_everything(published):
    st = status(published)
    assert st["status"] == "warning"  # swim season + winter break notices
    assert st["stale"] is False
    assert st["source"]["schedule_url"] == FALL_URL
    assert len(st["source"]["sha256"]) == 64
    assert st["validity"] == {"start": "2026-10-03", "end": "2026-12-31", "basis": st["validity"]["basis"],
                              "uncertain": True}
    assert st["feeds"]["all"]["events"] == sum(st["feeds"][k]["events"] for k in st["feeds"] if k != "all")
    assert st["withheld"]["lap_13"] > 0
    for f in FEEDS:
        assert (published / f).read_text().startswith("BEGIN:VCALENDAR")
    assert (published / "data" / "schedule.json").exists()


def test_rerun_with_same_source_is_idempotent(published, cfg, make_fetcher):
    before = snapshot(published)
    code, st = cli.refresh(cfg, published, make_fetcher(), NOW + timedelta(hours=1), TODAY)
    assert code == 0 and st["changed_files"] == []
    assert snapshot(published) == before
    assert st["change"] == {"url_changed": False, "content_changed": False, "parser_changed": False,
                            "reparsed": True}


def test_new_link_on_landing_page_is_followed_and_published(published, cfg, landing_html, make_fetcher):
    new = "https://cms-assets.ncprd.org/Fall_2026_NCAP_Hours_Schedule_REVISED_123abc.pdf"
    fetcher = make_fetcher(landing=landing_html.replace(FALL_URL, new), pdf_url=new)
    code, st = cli.refresh(cfg, published, fetcher, NOW + timedelta(days=1), TODAY)
    assert code == 0
    assert new in fetcher.calls
    assert st["change"]["url_changed"] is True
    assert st["source"]["schedule_url"] == new
    assert new.encode() in (published / "pool_all.ics").read_bytes().replace(b"\r\n ", b"")
    assert st["source_history"][0]["schedule_url"] == new


def test_changed_bytes_at_same_url_trigger_reparse(published, cfg, fall_pdf, make_fetcher, monkeypatch):
    parsed = []
    real = cli.extract_pdf
    monkeypatch.setattr(cli, "extract_pdf", lambda b: parsed.append(b) or real(b))
    changed = fall_pdf + b"\n% revised\n"
    code, st = cli.refresh(cfg, published, make_fetcher(pdf=changed), NOW + timedelta(days=1), TODAY)
    assert code == 0
    assert parsed == [changed]
    assert st["change"]["content_changed"] is True and st["change"]["url_changed"] is False
    assert st["source"]["sha256"] != status(published)["source_history"][-1]["sha256"]
    assert len(st["source_history"]) == 2


def test_html_error_instead_of_pdf_keeps_last_good(published, cfg, make_fetcher):
    before = snapshot(published)
    fetcher = make_fetcher(pdf=b"<!DOCTYPE html><html><body>Service Unavailable</body></html>", pdf_type="text/html")
    code, st = cli.refresh(cfg, published, fetcher, NOW + timedelta(days=1), TODAY)
    assert code == 1
    assert_last_good_kept(published, before, st)


def test_landing_page_without_schedule_keeps_last_good(published, cfg, landing_html, make_fetcher):
    before = snapshot(published)
    html = landing_html.replace("Aquatic Park Drop-in Schedule and Rates", "Celebration Room Rentals")
    html = html.replace(FALL_URL, "https://ncprd.org/rentals")
    code, st = cli.refresh(cfg, published, make_fetcher(landing=html), NOW + timedelta(days=1), TODAY)
    assert code == 1
    assert "no drop-in schedule link" in st["errors"][0]
    assert_last_good_kept(published, before, st)


def test_landing_fetch_failure_keeps_last_good(published, cfg, make_fetcher):
    before = snapshot(published)
    fetcher = make_fetcher()
    del fetcher.routes["https://ncprd.org/aquatic-park"]
    code, st = cli.refresh(cfg, published, fetcher, NOW + timedelta(days=1), TODAY)
    assert code == 1 and "FetchError" in st["errors"][0]
    assert_last_good_kept(published, before, st)


HEADER = ["", "SUN", "MON", "TUE", "WED", "THUR", "FRI", "SAT"]


def _fake_pdf(monkeypatch, tables, text="Fall 2026 Time/Date: 12:30-3:30pm Sa/Su 10/3-12/31"):
    monkeypatch.setattr(cli, "extract_pdf", lambda b: PdfContent(tables=tables, text=text))


def test_ambiguous_table_keeps_last_good(published, cfg, make_fetcher, fall_content, monkeypatch):
    before = snapshot(published)
    grid = fall_content.tables[0]
    _fake_pdf(monkeypatch, [grid, grid])
    code, st = cli.refresh(cfg, published, make_fetcher(pdf=b"%PDF-1.7 changed" + b"x" * 20000),
                           NOW + timedelta(days=1), TODAY)
    assert code == 1 and "refusing to guess" in st["errors"][0]
    assert_last_good_kept(published, before, st)


def test_clearly_invalid_hours_keep_last_good(published, cfg, make_fetcher, fall_content, monkeypatch):
    before = snapshot(published)
    grid = [list(r) for r in fall_content.tables[0]]
    grid[2][2] = "1AM-11:30PM"  # 22.5 hour lap swim
    _fake_pdf(monkeypatch, [grid])
    code, st = cli.refresh(cfg, published, make_fetcher(pdf=b"%PDF-1.7 changed" + b"x" * 20000),
                           NOW + timedelta(days=1), TODAY)
    assert code == 1 and "implausible session length" in st["errors"][0]
    assert_last_good_kept(published, before, st)


def test_nearly_empty_grid_keeps_last_good(published, cfg, make_fetcher, monkeypatch):
    before = snapshot(published)
    _fake_pdf(monkeypatch, [[HEADER, ["DEEP H2O", "", "9-10AM", "", "", "", "", ""]]])
    code, st = cli.refresh(cfg, published, make_fetcher(pdf=b"%PDF-1.7 changed" + b"x" * 20000),
                           NOW + timedelta(days=1), TODAY)
    assert code == 1 and "weekly sessions parsed" in st["errors"][0]
    assert_last_good_kept(published, before, st)


def test_large_event_drop_requires_override(published, cfg, make_fetcher, fall_content, monkeypatch):
    before = snapshot(published)
    grid = [list(r) for r in fall_content.tables[0]]
    # Remove most sessions but keep the table valid: blank out Mon-Thu for every row.
    for r in grid[1:]:
        for i in (2, 3, 4, 5):
            r[i] = ""
    _fake_pdf(monkeypatch, [grid], text=" ".join(fall_content.text.split()))
    fetcher = make_fetcher(pdf=b"%PDF-1.7 changed" + b"x" * 20000)
    code, st = cli.refresh(cfg, published, fetcher, NOW + timedelta(days=1), TODAY)
    assert code == 1 and "would drop" in st["errors"][0]
    assert_last_good_kept(published, before, st)
    code, st = cli.refresh(cfg, published, fetcher, NOW + timedelta(days=1), TODAY, allow_large_change=True)
    assert code == 0 and snapshot(published) != before


def test_expired_schedule_keeps_last_good(published, cfg, make_fetcher):
    before = snapshot(published)
    code, st = cli.refresh(cfg, published, make_fetcher(), NOW + timedelta(days=80), date(2027, 1, 15))
    assert code == 1 and "expired" in st["errors"][0]
    assert snapshot(published) == before


def test_status_marks_stale_after_repeated_failures(published, cfg, make_fetcher):
    fetcher = make_fetcher()
    del fetcher.routes["https://ncprd.org/aquatic-park"]
    _, st = cli.refresh(cfg, published, fetcher, NOW + timedelta(days=3), TODAY + timedelta(days=3))
    assert st["stale"] is True
    assert st["last_success"] == NOW.isoformat()


def test_dry_run_writes_nothing(tmp_path, cfg, make_fetcher):
    code, _ = cli.refresh(cfg, tmp_path, make_fetcher(), NOW, TODAY, dry_run=True)
    assert code == 0 and list(tmp_path.iterdir()) == []


def test_no_hardcoded_schedule_url_in_config_or_source():
    for path in [ROOT / "config" / "config.toml", *(ROOT / "src").rglob("*.py")]:
        text = path.read_text()
        assert "cms-assets" not in text and ".pdf\"" not in text, path
