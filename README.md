# Yale Events

Yale events are scattered across dozens of department calendars, school sites, residential college
calendars, and Yale Connect. This project scrapes them into one normalized database and serves it as:

- a **JSON API** filterable by date, category, location, college/department/organization, and text,
- a **subscribable iCal feed** for any filtered view (`/events.ics?...`), and
- a **browser UI** at `/` built on the same public endpoints.

Around 1,800 upcoming events from 20 sources, refreshed every 4 hours, with cross-listed duplicates merged.

## Quick start

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
source .venv/bin/activate
export YEV_CONTACT=you@yale.edu   # sent in the User-Agent so site admins can reach you
yev scrape                         # fetch all enabled sources (a few minutes at 1 request/second)
yev serve                          # browse at http://127.0.0.1:8000, interactive API docs at /docs
```

If `yev` fails with `ModuleNotFoundError: yale_events`, prefix commands with `PYTHONPATH=src`
(macOS sometimes hides the venv's `.pth` files, so the editable install isn't picked up).

Run the tests with `pytest`. They are offline: every adapter is tested against saved pages with HTTP mocked.

## API

| Endpoint | |
|---|---|
| `GET /events` | Events soonest first; the next 90 days by default. Paginate with the returned `next_cursor`. |
| `GET /events.ics` | The same filters as a calendar to subscribe to (one week back onward; cancelled events are marked `STATUS:CANCELLED` so subscribed calendars update). |
| `GET /events/{id}`, `/events/{id}.ics` | One event, as JSON or as a single-event calendar file. |
| `GET /categories`, `/areas`, `/locations`, `/orgs`, `/sources` | Valid filter values, each with its count of upcoming events. |

Filters (all optional, on both `/events` and `/events.ics`; list filters accept repeats or commas and match any value):

| Filter | Example | |
|---|---|---|
| `start`, `end` | `start=2026-10-01&end=2026-10-31` | Dates (end inclusive) or ISO datetimes, New Haven time. |
| `category` | `category=talks,music` | Talks, music, arts & performance, film, exhibitions, academic, career, social, cultural, religious, sports, health, community service. |
| `area` | `area=science-hill` | Campus area: central, arts district, Science Hill, medical, West Campus, athletic fields, off campus, online. |
| `location` | `location=woolsey-hall` | A specific building (~110 known, with aliases, room codes, and addresses). |
| `org` | `org=computer-science,pauli-murray` | A residential college, department or school, or other organization. |
| `source` | `source=law` | The calendar it came from. |
| `q` | `q=pizza` | Text search in title, description, and venue. |
| `free_food` | `free_food=true` | The listing mentions food ("lunch provided", "refreshments", ...). |
| `include_ongoing`, `include_cancelled` | | Daily occurrences of long exhibitions, and cancelled events, are hidden by default. |

Examples:

```sh
curl 'localhost:8000/events?category=talks&area=science-hill&free_food=true'
curl 'localhost:8000/events?org=pauli-murray&start=2026-10-01&end=2026-10-07'
# Subscribe in Google/Apple Calendar to a filtered view:
#   http://<host>/events.ics?category=music&org=music
```

Each event has a stable `id`, `title`, `description`, `start`/`end` (with offset) and `all_day`,
a normalized `location` (`name`, `room`, `building`, `area`, `lat`/`lon`, `virtual`), `categories`,
`free_food`, `cost`, `cancelled`, `url`, `image_url`, `groups` (hosts), `source`, and `also_listed_by`
(other sources that list the same event). Full schemas are at `/docs`.

## Sources

| Kind | Sources |
|---|---|
| Central calendar | events.yale.edu (Localist API, all ~187 department and office groups) |
| Schools and departments | Medicine (incl. Public Health, Child Study Center), Law, Music, Architecture, Nursing, Engineering, Jackson School, MacMillan Center (area-studies councils) |
| Museums and offices | Yale Center for British Art, Peabody Museum, Chaplain's Office, OISS, Athletics |
| Residential colleges | Benjamin Franklin, Pauli Murray, Silliman, Timothy Dwight, Davenport |
| Student organizations | Yale Connect, the site-wide student-organization platform (OrgHub's domain no longer resolves; Yale Connect replaced it) |

Every source, including ones checked and rejected, is recorded in [`sources.yaml`](sources.yaml) with
notes on how it's read and why. Sites are read through the best interface each offers, one adapter per
format rather than per site: `localist` (Localist API), `ical` (any .ics, including public Google
Calendars), `jsonld` (schema.org Event data in pages), `yalesites` and `drupal-calendar` (Yale's Drupal
platforms), `ysm` (the School of Medicine's JSON API), `engineering`, and small HTML parsers for
`yale-music`, `peabody`, `macmillan`, and `ycba`. `yev discover <url>` looks for feeds on a new site.

Coverage notes:
- Five residential colleges publish a calendar the scraper can read (Davenport's is currently empty).
  The other nine were checked: they have no events page, a calendar that isn't public, or one with no
  events (see `sources.yaml`). All 14 still appear in the college filter, which also matches events
  held in each college's buildings, found through the central calendar and other sources.
- Yale Connect hides most venues from signed-out readers. Those events are included without a venue
  when their type is usually open to all (talks, performances, debates, tours, ...), and skipped when
  it's usually internal (meetings, rehearsals, study halls).
- Events announced only through Google Forms or email lists have no feed to read and aren't covered.

## How it works

```
sources.yaml → adapter → RawEvent → normalize → SQLite → dedupe → FastAPI (/events, /events.ics, /)
```

1. **Fetch.** Every request goes through one client that waits at least 1 second between requests,
   retries timeouts, and saves each raw response to `data/cache/`.
2. **Normalize.** Times become UTC. Venue text ("Room HQ L01 in the Humanities Quadrangle (320 York
   Street)") resolves to a building and campus area via `src/yale_events/data/locations.yaml`.
   Categories come from source tags, then keyword rules in `categories.yaml`. Free food is detected
   from the description.
3. **Store.** One row per occurrence (recurring events are expanded), with an ID derived from the
   source's own ID, so re-scrapes update in place. Events a source stops listing are marked stale.
4. **Dedupe.** The same event on several calendars (a concert on events.yale.edu and music.yale.edu) is
   merged by fuzzy title match, start within 15 minutes, and non-conflicting venue. The richest source
   is kept and the others appear as `also_listed_by`.
5. **Serve.** One query builder backs both the JSON feed and the iCal feed, so every filter works on both.

Colleges, departments, and organizations for the `org` filter are defined in
`src/yale_events/data/organizations.yaml`: an organization matches by source, by host group, or by
building. `yev uncategorized` lists events and hosts the rules don't cover yet.

Rebuild from the cache without touching the network after changing a rule:

```sh
yev scrape --replay && yev uncategorized
```

## Scheduling and health

```sh
yev schedule install --every 4   # launchd agent (macOS): scrape now and every 4h, then check sources
yev schedule status              # last exit code and the tail of data/logs/scrape.log
yev schedule uninstall
yev sources check                # exits 1 if a source is failing or overdue
```

`sources check` flags a source whose recent runs failed, that has no successful scrape in 12 hours,
that suddenly returns zero events or under half its usual number, or where many events vanished at
once. When scheduled, it posts a macOS notification if anything needs attention. Elsewhere, run
`yev scrape && yev sources check` from cron.

## Scraping policy

Requests are rate-limited to one per second per run and identify the project in the User-Agent.
robots.txt is respected (drama.yale.edu is excluded because of it), with two exceptions: the
events.yale.edu Localist API and public Google Calendar feeds, both fetched once per scheduled run.
No per-event pages are fetched from events.yale.edu.

## License

[MIT](LICENSE) covers the code. Event listings belong to the calendars they come from; each event
links back to its source page.
