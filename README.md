# ncprd-aquatic-park-calendar

Google-Calendar-subscribable `.ics` feeds of the **drop-in pool sessions at North Clackamas Aquatic Park**
(lap swim, dive well, Deep H2O, H2O Fit, Big Surf recreation swim, parent-preschool swim). A daily GitHub
Action rebuilds the feeds from the official NCPRD schedule.

The schedule PDF changes name every season. This repo finds the current one on
<https://ncprd.org/aquatic-park> each day, so you never have to upload a CSV or update a URL by hand.

> Unofficial. Schedules are subject to change, so check with NCPRD before you go.

## Subscribe

Once GitHub Pages is enabled (see [Setup](#setup)), the site is at
`https://<owner>.github.io/ncprd-aquatic-park-calendar/`. The steps below assume the `klove2016` account;
swap in your own owner if it's different.

| Feed | URL |
|---|---|
| All drop-in sessions | `https://klove2016.github.io/ncprd-aquatic-park-calendar/pool_all.ics` |
| Lap Pool Fitness (13+ and all-ages) | `…/pool_lap.ics` |
| Dive Well Fitness | `…/pool_dive.ics` |
| Deep H2O + H2O Fit | `…/pool_h2o.ics` |
| Big Surf Recreation Swim | `…/pool_recreation.ics` |
| Parent Preschool Swim | `…/pool_preschool.ics` |

The site has **Add to Google**, `webcal://`, and copy-URL buttons for each feed. It also shows a live health and
provenance panel built from `status.json`. To subscribe in Google Calendar by hand: *Settings → Add calendar → From
URL*, then paste the `https://…ics` URL. Google refreshes subscriptions on its own timetable, typically every 8–24 hours.

## How it works

```
daily cron / manual dispatch
  └─ fetch ncprd.org/aquatic-park (UA, timeouts, retries + backoff, rate limit)
      └─ score every <a> by anchor text + href  → best "drop-in schedule" link (no hardcoded URL)
          └─ download, require %PDF-, SHA-256, compare URL + hash with status.json
              └─ parse the weekly grid (pdfplumber tables) + dated notes (closures, exceptions, date ranges)
                  └─ validate schedule → expand 90-day horizon → stage ICS → validate ICS + event counts
                      ├─ all OK  → atomically replace docs/*.ics, update status.json, commit
                      └─ any failure → keep last-good feeds, status.json = error/stale, fail job, open issue
```

| Module | Role |
|---|---|
| `src/ncprd_pool/fetch.py` | HTTP with identifying UA, (connect, read) timeouts, urllib3 retry/backoff on 429/5xx, min interval between requests, size cap |
| `src/ncprd_pool/discover.py` | Landing-page sanity check, link scoring (`config.toml [discovery]`), relative-URL and redirect resolution, ambiguity detection, one-level follow when a link lands on an HTML page |
| `src/ncprd_pool/parse.py` | PDF → weekly grid (day columns × category rows), time-range parsing (`6-7:55AM`, `12:30-3:30PM`, `11AM-NOON`, wrapped `8:30AM-\n3:30PM`), closures, exception windows, per-session date ranges, validity window, season/year inference |
| `src/ncprd_pool/occurrences.py` | Dated, DST-correct (`zoneinfo`) occurrences; closures removed; exception windows withheld; stable UIDs; carry-over of the previous schedule when a new season is linked early |
| `src/ncprd_pool/ics.py` | Deterministic RFC 5545 writer (TZID + VTIMEZONE, escaping, 75-octet folding, CRLF) |
| `src/ncprd_pool/validate.py` | Pre-publication checks (see below) |
| `src/ncprd_pool/cli.py` | The pipeline and `status.json` |

### What gets published and what gets withheld

* **Only the drop-in session grid** is turned into events. General facility or business hours, fees, and
  membership text are ignored.
* **Facility closures** listed in the schedule (Fall 2026: 8/24–10/2, 11/11, 11/26–11/27, 12/24–12/25, 1/1)
  have no events.
* **Exception windows.** A sentence that pairs a date range with wording like *adjusted / modified / reduced /
  cancelled / closed* becomes a withheld window. It applies to the categories the sentence names, or to *all*
  categories if it names none. Fall 2026 has *"Lap Pool and Dive Well availability will be adjusted due to
  High School Swim Season 11/16/26-2/19/27"*, so lap and dive sessions from 11/16 on are **not published**. The
  number withheld is reported in `status.json`. Other categories are unaffected.
* **Per-program date ranges** such as Big Surf's *"Time/Date: 12:30-3:30pm Sa/Su 10/3-12/31"* limit only the
  program whose grid sessions match that time and those days.
* **Labelled special periods without times** (e.g. *"WINTER BREAK: 12/21-12/23, 12/28-12/31"*) produce a warning. No
  extra sessions are invented for them.
* **Any other dated text** that the parser cannot classify is shown as a warning, so it never goes unnoticed.
* **Validity window.** An explicit statement (*"Schedule dates: 1/4-3/20"*) is used when the PDF has one.
  Otherwise the window is inferred from dated ranges, the reopening after a long closure, or the conventional
  season, and is marked `"uncertain": true` with its basis. It is always capped at `max_validity_days`
  (default 120). Events are never generated past it. For Fall 2026 the inferred window is 2026-10-03 → 2026-12-31.
* **New season linked early.** If NCPRD links next season's PDF before the current season ends, the previously
  validated schedule (`docs/data/previous_schedule.json`) still covers the dates before the new one starts.

### Pre-publication validation (fail ⇒ last-good feeds kept)

* Landing page is HTML, mentions "Aquatic Park", and has exactly one best schedule link (none or a tie fails)
* Downloaded document starts with `%PDF-` and is a plausible size (an HTML error page fails)
* Exactly one weekly grid, with each weekday exactly once in its header (zero or two grids fail)
* Every cell in a recognised row parses completely, and no row matches two categories
* At least 15 weekly sessions across at least 4 categories
* Session lengths 20–600 min, all between 05:00 and 23:00, no overlaps within a category and day
* Validity window coherent (≤ 200 days); schedule not expired; at least one occurrence in the window
* Generated ICS re-parsed with `icalendar`; event count and UIDs match; every DTSTART/DTEND matches the
  expected instant **and** local wall-clock time (catches DST/offset errors)
* Over the same dates, the new feed must not have more than 50% fewer events than the published feed. A real
  drop can be accepted with the `allow_large_change` workflow input (or `--allow-large-change`).

### `docs/status.json`

`status` (`ok` / `warning` / `error`), `stale`, `last_checked`, `last_success`, `last_published`,
`source` (schedule URL, link text, SHA-256, size, fetch time, first-seen, parser version), `change`
(URL/content/parser changed), `validity` (+ `basis`, `uncertain`), `horizon`, `feeds` (event counts),
`closures`, `exceptions`, `withheld`, `warnings`, `errors`, `source_history`, `limitations`.
`docs/data/schedule.json` holds the full parsed weekly schedule of the published source.

UIDs are `sha256(category | local start | local end)`. They stay the same across re-parses, URL changes and
daily horizon shifts, so calendar apps update events in place instead of duplicating them. Output is
byte-deterministic, so a commit only happens when something actually changed (plus the daily `status.json`
heartbeat).

## Setup

1. Create an **empty** GitHub repo named `ncprd-aquatic-park-calendar`, then push:
   ```bash
   cd ncprd-aquatic-park-calendar
   git init -b main && git add . && git commit -m "Initial import"
   git remote add origin git@github.com:<owner>/ncprd-aquatic-park-calendar.git
   git push -u origin main
   ```
2. **Settings → Pages →** *Deploy from a branch*, branch `main`, folder **`/docs`**.
3. **Settings → Actions → General**: allow Actions. The workflow declares its own minimal
   permissions (`contents: write` to commit `docs/`, `issues: write` for alerts), so you don't need to change the
   default token permissions.
4. **Actions → Refresh pool calendar → Run workflow** once to confirm it works end to end.
5. Optional: set the repository variable `ALERT_ISSUES=false` to turn off the failure-alert issue. The job still
   fails, and GitHub emails you about failed runs by default.

No secrets or credentials are needed.

## Local commands

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

python -m pytest -q                                   # full test suite (offline)
PYTHONPATH=src python -m ncprd_pool --dry-run         # live fetch + parse + validate, writes nothing
PYTHONPATH=src python -m ncprd_pool                   # live refresh → docs/
PYTHONPATH=src python -m ncprd_pool \
  --landing-file tests/fixtures/landing_fall_2026.html \
  --pdf-file tests/fixtures/fall_2026_ncap_schedule.pdf \
  --today 2026-10-28 --docs /tmp/pool-docs            # fully offline run from fixtures
```

### Smoke test

```bash
PYTHONPATH=src python -m ncprd_pool --dry-run; echo "exit=$?"          # expect exit=0, ~238 events in pool_all
python -c "import icalendar,sys; c=icalendar.Calendar.from_ical(open('docs/pool_all.ics','rb').read()); print(len(c.walk('VEVENT')),'events')"
python -m http.server -d docs 8000   # open http://localhost:8000 to see the subscribe page + health panel
```

## Tests

`tests/fixtures/fall_2026_ncap_schedule.pdf` is the official Fall 2026 PDF, used as a **test fixture only**.
`tests/fixtures/landing_fall_2026.html` is a saved copy of the landing page.

| Requirement | Test |
|---|---|
| New landing-page link → new parse + publish | `test_pipeline.py::test_new_link_on_landing_page_is_followed_and_published` |
| Same URL, changed bytes → reparse | `test_changed_bytes_at_same_url_trigger_reparse` |
| HTML error, missing schedule, ambiguous table, invalid hours can't corrupt feeds | `test_html_error_instead_of_pdf_keeps_last_good`, `test_landing_page_without_schedule_keeps_last_good`, `test_ambiguous_table_keeps_last_good`, `test_clearly_invalid_hours_keep_last_good`, `test_nearly_empty_grid_keeps_last_good`, `test_large_event_drop_requires_override`, `test_expired_schedule_keeps_last_good` |
| Golden parse of Fall 2026 PDF | `test_parse.py::test_golden_fall_2026_grid`, `test_golden_fall_2026_dates` |
| Effective dates, closures, exceptions | `test_occurrences.py::test_horizon_and_validity_bounds`, `test_closures_have_no_sessions`, `test_swim_season_withholds_lap_and_dive_only`, `test_big_surf_starts_with_its_date_range` |
| Stable UIDs, DST | `test_uids_stable_and_unique`, `test_dst_transition_keeps_local_wall_time`, `test_dst_round_trip_through_ics` |
| No hardcoded schedule URL | `test_no_hardcoded_schedule_url_in_config_or_source` |

## Limitations

* The source is checked **once a day**. A change shows up only after NCPRD publishes it on ncprd.org, and
  calendar apps add their own refresh delay.
* Times in announced exception windows (e.g. the high-school swim season) are **withheld**, not guessed. They
  come back only when an official schedule publishes them.
* Only PDF schedules with a day-of-week grid are supported. If NCPRD switches to a very different format, the
  run fails visibly and keeps the last-good feeds. Update `parse.py` and add a new golden fixture.
* GitHub disables scheduled workflows in repos with no activity for 60 days. The daily `status.json` commit
  normally prevents that, but if it does happen, re-enable the workflow from the Actions tab.
