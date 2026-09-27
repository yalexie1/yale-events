"""Adapter for peabody.yale.edu/events/upcoming (Drupal view, no feed, one page).

The list is grouped by month ("October 2026"), then by day ("Saturday, October 3"), and each item
has a time range ("11:00 am – 12:00 pm"), an event type, a title link, a snippet, and admission
text. Items under the "Ongoing" group have no date and are skipped.
"""

import html
import re
from datetime import date, datetime, time
from urllib.parse import urljoin

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.text import clean_description, clean_field
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

_TOKEN = re.compile(
    r'<h2 class="grouping-primary">(?P<month>.*?)</h2>'
    r'|<h3 class="item-list-upcoming-day-title">(?P<day>.*?)</h3>'
    r'|<li class="item-upcoming-event">(?P<item>.*?)</li>',
    re.S,
)
_TIME = re.compile(r'item-upcoming-event-time">(.*?)</span>', re.S)
_TYPE = re.compile(r'item-upcoming-event-type">(.*?)</p>', re.S)
_TITLE = re.compile(r'item-upcoming-event-title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET = re.compile(r'item-upcoming-event-snippet">(.*?)</p>', re.S)
_ADMISSION = re.compile(r'item-upcoming-event-admission-type">(.*?)</span>', re.S)
_IMAGE = re.compile(r"background-image:url\('([^']+)'\)")
_RANGE = re.compile(r"(\d{1,2}(?::\d{2})?\s*[ap]m)(?:\s*[–-]\s*(\d{1,2}(?::\d{2})?\s*[ap]m))?", re.I)


class PeabodyAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        page = self.http.get_text(source.url, cache_name=CacheNamer(source.id).next("html"))
        events = parse_listing(page, source.url)
        start = datetime.combine(datetime.now(NEW_HAVEN).date(), time(), NEW_HAVEN)
        end = max((e.start for e in events), default=start)
        return FetchResult(events=[e for e in events if e.start >= start], window_start=start, window_end=end)


def _text(fragment: str | None) -> str | None:
    return clean_field(html.unescape(re.sub(r"<[^>]+>", " ", fragment))) if fragment else None


def parse_listing(page: str, base_url: str) -> list[RawEvent]:
    events: list[RawEvent] = []
    year: int | None = None
    day: date | None = None
    for m in _TOKEN.finditer(page):
        if m["month"] is not None:
            label = _text(m["month"]) or ""
            year = int(y.group()) if (y := re.search(r"\b\d{4}\b", label)) else None
            day = None
        elif m["day"] is not None:
            try:
                day = datetime.strptime(f"{_text(m['day'])} {year}", "%A, %B %d %Y").date() if year else None
            except ValueError:
                day = None
        elif day is not None and (ev := parse_item(m["item"], day, base_url)):
            events.append(ev)
    return events


def parse_item(item: str, day: date, base_url: str) -> RawEvent | None:
    title = _TITLE.search(item)
    if not title:
        return None
    start = datetime.combine(day, time(), NEW_HAVEN)
    end, all_day = None, True
    if (t := _TIME.search(item)) and (r := _RANGE.search(_text(t.group(1)) or "")):
        start, all_day = datetime.combine(day, _clock(r.group(1)), NEW_HAVEN), False
        end = datetime.combine(day, _clock(r.group(2)), NEW_HAVEN) if r.group(2) else None
    url = urljoin(base_url, title.group(1))
    admission = _text(_ADMISSION.search(item).group(1)) if _ADMISSION.search(item) else None
    kind = _text(_TYPE.search(item).group(1)) if _TYPE.search(item) else None
    image = _IMAGE.search(item)
    return RawEvent(
        source_event_id=f"{title.group(1)}@{start.isoformat()}",  # recurring programs share a URL
        title=_text(title.group(2)),
        description=clean_description(_SNIPPET.search(item).group(1)) if _SNIPPET.search(item) else None,
        start=start,
        end=end,
        all_day=all_day,
        url=url,
        image_url=urljoin(base_url, image.group(1)) if image else None,
        tags=[kind] if kind else [],
        free=True if admission and admission.lower().startswith("free") else None,
        cost=admission,
    )


def _clock(s: str) -> time:
    s = s.replace(" ", "").lower()
    return datetime.strptime(s, "%I:%M%p" if ":" in s else "%I%p").time()
