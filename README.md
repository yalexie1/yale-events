# Yale Events

One normalized feed of Yale events from the central calendar, school and department calendars,
athletics, and residential colleges. Filter it by date, category, and location as JSON, or subscribe to
any filtered view as an iCal calendar.

## Setup

```sh
uv sync
source .venv/bin/activate
export YEV_CONTACT=you@yale.edu   # sent in the User-Agent so site admins can reach you
yev scrape                         # fetch all enabled sources (~2 min, 1 request/second)
yev serve                          # API at http://127.0.0.1:8000, docs at /docs
```

If `yev` fails with `ModuleNotFoundError: yale_events`, prefix commands with `PYTHONPATH=src`.
macOS sometimes hides the venv's `.pth` files, so the editable install isn't picked up.

## API

| Endpoint | |
|---|---|
| `GET /events` | Next 14 days by default. Filters: `start`, `end`, `category`, `area`, `location`, `source`, `q`, `free_food`, `include_ongoing`, `include_cancelled`. Paginate with `cursor`. |
| `GET /events.ics` | The same filters as a subscribable calendar, e.g. `/events.ics?category=talks&area=science-hill` |
| `GET /events/{id}`, `/events/{id}.ics` | One event |
| `GET /categories`, `/areas`, `/locations`, `/sources` | Valid filter values, each with an upcoming-event count |

## Sources

`sources.yaml` lists every source with notes on how it's read. Switch one off or on with
`enabled`. The adapters are `localist`, `ical`, `engineering`, `yalesites`, `ysm`, `yale-music`, and `drupal-calendar`. `yev discover <url>` looks for
feeds on a new site. Building aliases and campus areas are in `src/yale_events/data/locations.yaml`, and
category rules are in `src/yale_events/data/categories.yaml`. `yev uncategorized` shows what the rules miss.

## Scheduling and health

```sh
yev schedule install --every 4   # launchd agent: scrape now and every 4h, then check sources
yev schedule status              # last exit code and the tail of data/logs/scrape.log
yev schedule uninstall
yev sources check                # exits 1 if a source is failing or overdue
```

`sources check` flags these problems:
- a source whose recent runs failed
- a source with no successful scrape in 12h
- a source that suddenly returns 0 events, or under half its usual number
- a source where many events vanished at once

When scheduled, it posts a macOS notification if anything needs attention.

Raw responses are cached in `data/cache/`, keeping the last 3 runs per source. `yev scrape --replay` rebuilds from
that cache without touching the network. Use it after changing normalization rules.
