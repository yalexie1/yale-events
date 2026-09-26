"""Adapter for YaleSites (Yale's Drupal platform) sites that post events as pages.

The source URL is a page that links to events (/events/YYYY-MM-DD-slug), often the homepage. Each
event page has an `event-meta` block with <time datetime> for every upcoming date and an
"Add to Calendar" link holding a base64 iCal, which gives the duration.

Options:
  days: how far ahead to keep occurrences (default 180)
  max_events: cap on event pages fetched per run (default 40)
"""

import base64
import re
from datetime import datetime, time, timedelta
from urllib.parse import urljoin, urlparse

from icalendar import Calendar

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.text import clean_description, clean_field
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

DEFAULT_DAYS = 180
DEFAULT_MAX_EVENTS = 40

_EVENT_LINK = re.compile(r"""href=["']([^"']*/events/\d{4}-\d{2}-\d{2}-[^"'#?]+)["']""")
_TIME = re.compile(r'<time\s+datetime="([^"]+)"[^>]*>(.*?)</time>', re.S)
_ICS_DATA = re.compile(r'href="data:text/calendar;[^,]*base64,([^"]+)"')


class YaleSitesAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        names = CacheNamer(source.id)
        start = datetime.combine(datetime.now(NEW_HAVEN).date(), time(), NEW_HAVEN)
        end = start + timedelta(days=int(source.options.get("days", DEFAULT_DAYS)))
        listing = self.http.get_text(source.url, cache_name=names.next("html"))
        links = event_links(listing, source.url)[: int(source.options.get("max_events", DEFAULT_MAX_EVENTS))]
        events = []
        for url in links:
            html = self.http.get_text(url, cache_name=names.next("html"))
            events += [e for e in parse_event_page(html, url) if start <= e.start < end]
        return FetchResult(events=events, window_start=start, window_end=end)


def event_links(html: str, page_url: str) -> list[str]:
    host = urlparse(page_url).netloc
    urls = [urljoin(page_url, m) for m in _EVENT_LINK.findall(html)]
    return list(dict.fromkeys(u for u in urls if urlparse(u).netloc == host))


def _embedded_ics(html: str):
    for encoded in _ICS_DATA.findall(html):
        try:
            cal = Calendar.from_ical(base64.b64decode(encoded))
        except ValueError:
            continue
        for v in cal.walk("VEVENT"):
            return v
    return None


def parse_event_page(html: str, url: str) -> list[RawEvent]:
    """One RawEvent per date listed on the page (recurring events list all upcoming dates)."""
    meta_start = html.find('class="event-meta"')
    if meta_start < 0:
        return []
    meta = html[meta_start:]
    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", meta, re.S)
    title = clean_field(title_m.group(1)) if title_m else None
    if not title:
        return []

    dates_m = re.search(r'class="event-meta__multiple-dates"[^>]*>(.*?)</ul>', meta, re.S)
    times = _TIME.findall(dates_m.group(1) if dates_m else meta)[: 1 if not dates_m else None]
    if not times:
        return []

    ics = _embedded_ics(meta)
    duration, all_day = None, False
    if ics is not None:
        s = ics["DTSTART"].dt
        all_day = not isinstance(s, datetime)
        if not all_day and "DTEND" in ics:
            duration = ics["DTEND"].dt - s
        elif not all_day and "DURATION" in ics:
            duration = ics["DURATION"].dt

    desc_m = re.search(r'class="event-meta__description"[^>]*>(.*?)</div>', meta, re.S)
    description = clean_description(desc_m.group(1)) if desc_m else None
    path = urlparse(url).path

    events = []
    for iso, label in times:
        start = datetime.fromisoformat(iso)
        day_long = all_day or "all day" in label.lower()
        events.append(
            RawEvent(
                source_event_id=f"{path}@{start.isoformat()}",
                title=title,
                description=description,
                start=start,
                end=start + duration if duration and not day_long else None,
                all_day=day_long,
                url=url,
                series_id=path if len(times) > 1 else None,
                series_first_date=datetime.fromisoformat(times[0][0]).date() if len(times) > 1 else None,
                series_last_date=datetime.fromisoformat(times[-1][0]).date() if len(times) > 1 else None,
            )
        )
    return events
