# ncprd-aquatic-park-calendar

Subscribable calendars of drop-in pool sessions at North Clackamas Aquatic Park, rebuilt daily from the
official [NCPRD schedule](https://ncprd.org/aquatic-park). Unofficial; confirm with NCPRD before you go.

**Subscribe:** <https://klove2016.github.io/ncprd-aquatic-park-calendar/>

| Calendar | Feed |
|---|---|
| All sessions | [pool_all.ics](https://klove2016.github.io/ncprd-aquatic-park-calendar/pool_all.ics) |
| Lap swim | [pool_lap.ics](https://klove2016.github.io/ncprd-aquatic-park-calendar/pool_lap.ics) |
| Dive well | [pool_dive.ics](https://klove2016.github.io/ncprd-aquatic-park-calendar/pool_dive.ics) |
| Deep H2O + H2O Fit | [pool_h2o.ics](https://klove2016.github.io/ncprd-aquatic-park-calendar/pool_h2o.ics) |
| Big Surf open swim | [pool_recreation.ics](https://klove2016.github.io/ncprd-aquatic-park-calendar/pool_recreation.ics) |
| Parent-preschool swim | [pool_preschool.ics](https://klove2016.github.io/ncprd-aquatic-park-calendar/pool_preschool.ics) |

In Google Calendar: *Settings → Add calendar → From URL*, then paste a feed URL.

## How it works

A daily GitHub Action finds the current schedule PDF linked on ncprd.org (the URL changes every season),
parses the weekly grid, removes closure dates, and publishes 90 days of sessions to `docs/`.

- Sessions in announced exception windows (e.g. high-school swim season) are withheld, not guessed.
- If anything looks wrong (missing link, unparseable PDF, big drop in events), the last good feeds stay up,
  `status.json` is marked stale, the job fails and opens an issue.
- Re-run by hand from the Actions tab; use `allow_large_change` to accept a real schedule cut.

## Local

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python -m pytest -q                            # offline tests
PYTHONPATH=src python -m ncprd_pool --dry-run  # live check, writes nothing
PYTHONPATH=src python -m ncprd_pool            # refresh docs/
```
