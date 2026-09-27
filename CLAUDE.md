# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Yale events aggregator: scrapers feed a normalized SQLite DB, and a FastAPI app serves it as a filterable JSON feed and as subscribable iCal. Python 3.13, uv, SQLAlchemy 2, pydantic v2, typer, httpx.

## Commands

Shell state doesn't persist between commands, so start each one with `source .venv/bin/activate &&`. Also prefix Python commands with `PYTHONPATH=src`: macOS hides the venv's `.pth` files, so the editable install often isn't found (`ModuleNotFoundError: yale_events`). `python -m yale_events.cli ...` works the same as `yev ...`.

```sh
uv sync                                                   # install
PYTHONPATH=src python -m pytest -q                        # all tests (offline; HTTP mocked with respx)
PYTHONPATH=src python -m pytest tests/test_adapters.py::test_ical_options -q   # one test
PYTHONPATH=src python -m yale_events.cli scrape [--source ID]   # live scrape (1 req/s, ~2 min for all)
PYTHONPATH=src python -m yale_events.cli scrape --replay        # re-normalize from data/cache, no network
PYTHONPATH=src python -m yale_events.cli serve                  # API on :8000, docs at /docs
PYTHONPATH=src python -m yale_events.cli sources [check]        # last runs / health (exit 1 on errors)
PYTHONPATH=src python -m yale_events.cli uncategorized          # titles/locations the rules miss
PYTHONPATH=src python -m yale_events.cli discover URL...        # look for iCal/RSS/Google Calendar/Localist feeds
```

There is no linter config. Style: line length is about 120, and `# fmt: skip` is used on hand-aligned literals. Query the DB with the venv's Python (SQLAlchemy), not the `sqlite3` CLI.

## Architecture

The pipeline runs `sources.yaml → adapter → RawEvent → Normalizer → upsert → dedupe → API`.

- **Sources and adapters.** `sources.yaml` is both the config and the survey record: it holds disabled entries and comments on why each site is or isn't used. Adapters are picked by `type`, not by site: `localist` (events.yale.edu API), `ical` (any .ics, including public Google Calendars), `engineering` (a SEAS JSON endpoint plus detail pages), and `yalesites` (Drupal event pages), `ysm` (medicine.yale.edu JSON API), `jsonld` (any page with schema.org Event JSON-LD; try it first on a new site), `drupal-calendar`, and one-site HTML parsers (`yale-music`, `peabody`, `macmillan` (listing plus each event page, for venue and description), `ycba`, `tsai-city` (Tsai CITY's Luma iCal feed, with blurbs and images from city.yale.edu/events matched by Luma link)). They're registered in `adapters/__init__.py`. An adapter returns a `FetchResult(events, window_start, window_end)`. Per-source quirks go in `options` (for example the ical adapter's `location_contains`, `title_strip`, `exclude_title`, `hidden_location`), not in code.
- **HTTP.** Adapters make every request through `PoliteClient`, which enforces a minimum interval and writes each raw response to `data/cache/<source>-<stamp>-p<N>.<ext>`, named by `CacheNamer` in request order. `ReplayClient` serves the latest cached run back by the same names. An adapter must therefore make the same sequence of requests on every run for replay to work. `prune_cache` keeps 3 runs per source.
- **Normalization.** `normalize/` turns a RawEvent into Event column values. Times are stored as UTC through the `UTCDateTime` TypeDecorator (naive UTC in SQLite, tz-aware in Python); all-day events are local midnight. Locations resolve to canonical buildings and campus areas through aliases and room codes in `data/locations.yaml`, falling back to the source's `default_location`. Categories come from `data/categories.yaml`, first matching rule wins: source/event tags via `tag_map` (the source's `tags` count as event tags), then keyword regexes, then `default_category`. After changing a rule, run `scrape --replay` and `uncategorized`.
- **Storage.**
  - There is one `Event` row per occurrence; recurring events are expanded. The ID is `sha1(source:source_event_id)[:16]`, so `source_event_id` must stay stable across runs.
  - Events in the fetched window that a source stops listing are marked `stale`; nothing is deleted.
  - There are no migrations (`create_all` only). A schema change means rebuilding `data/events.db`, for example by moving it aside and running `scrape --replay`.
  - SQLite runs in WAL mode.
- **Dedupe.** `dedupe.py` runs after each scrape across sources. Two events are the same when:
  - their titles fuzzy-match (rapidfuzz),
  - they start within 15 minutes of each other,
  - and their locations don't conflict.

  In the same building within 5 minutes, one shared distinctive title word is enough ("Tenebrae" / "The Journey: 25 Years of Tenebrae"). Groups are formed with union-find, and one group can never hold two events from the same source. The canonical event belongs to the source listed earliest in `sources.yaml`. Every other event in the group gets `duplicate_of`: it is hidden from feeds and exposed as `also_listed_by`. Groups are recomputed each run.
- **API.** `api/query.py` builds one filtered query shared by `/events` (keyset cursor on `(start, id)`, default now → +90 days, hides ongoing and cancelled) and `/events.ics` (7 days back onward, includes cancelled events as STATUS:CANCELLED). Stale events and duplicates are always excluded. Text search (`q`, `search.py`) requires every word in the title, description, venue, or `groups`; a run of words naming something in `data/aliases.yaml` ("YPU" / "Yale Political Union") matches any name in its set, a building name or alias from `locations.yaml` also matches `location_id`, and known shorthands of ≤6 characters match as whole words (SQLite REGEXP, registered by SQLAlchemy's pysqlite dialect). The `org` filter (colleges, departments, other organizations) comes from `data/organizations.yaml`: an org matches by source, by one of the event's `groups` (exact, or `group_contains` for club-name suffixes), or by building; `uncategorized` lists groups not mapped there. Register `/events.ics` before `/events/{event_id}`. `/` serves `web/index.html`, a no-build vanilla JS page that uses only the public endpoints; it forces `[hidden] { display: none !important }`, so toggle visibility with `hidden` rather than inline styles.
- **Ops.** `health.py` produces `sources check`. `schedule.py` installs a launchd agent (`yev schedule install --every 4`) that runs `scrape` and then `sources check --notify`. The plist holds absolute paths, so after moving the repo run `schedule uninstall` and `schedule install` again. Keep the repo out of ~/Desktop, ~/Documents and ~/Downloads: macOS privacy controls block launchd jobs there ("getcwd: Operation not permitted" in `data/logs/scrape.log`).

## Scraping policy

- The user approved using the events.yale.edu Localist API lightly, and fetching public Google Calendar `.ics` feeds once per scheduled run, even though both sites' robots.txt disallow crawling.
- Don't add per-event page fetches against events.yale.edu.
- Otherwise respect robots.txt: `discover` checks it, and drama.yale.edu is excluded because of it.
- Yale Connect (CampusGroups site-wide iCal): ~83% of events hide their location ("Sign in to download the location") from anonymous readers. That doesn't mean members-only, so those are kept without a venue when their event type is usually open (talks, performances, debates, tours, ...) and skipped otherwise (meetings, rehearsals, socials, workshops); see `hidden_*` options in `sources.yaml` (user's choice, 2026-09-27). The feed is ~3.7 MB; fetch it once per run.
- Keep the 1 request per second interval.
- Before adding a department source, check whether its events are already in the central calendar: `yale-central` fetches all ~187 Localist groups, and many department sites (YaleSites pages with a Localist widget) just mirror it.
