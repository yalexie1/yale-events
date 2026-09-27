"""Adapter for britishart.yale.edu/exhibitions-programs (Yale Center for British Art; Drupal, no feed).

A paged grid (~11 per page, ~7 pages) of exhibitions and programs, soonest first. Each card links to
the item and shows its activity type ("Tour", "Family Program", "Exhibition"), title, image, and a
date line such as "Saturday, September 26, 2026, 10:30 am–12 pm ET" or "11–11:45 am ET". Exhibitions
(date ranges or "Ongoing") are skipped: they'd show as a single event on their opening day.
"""

import html
import re
from datetime import datetime, time
from urllib.parse import urljoin

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.text import clean_field
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

MAX_PAGES = 15
_CARD = re.compile(r'<a class="x-grid-advanced__grid-block x-grid-block" href="([^"]+)">(.*?)</a>', re.S)
_ACTIVITY = re.compile(r'x-grid-block__activity[^"]*">(.*?)</span>', re.S)
_TITLE = re.compile(r'x-grid-block__title">(.*?)</h4>', re.S)
_DATE = re.compile(r'x-grid-block__date[^"]*">(.*?)</p>', re.S)
_IMAGE = re.compile(r'<img[^>]+src="([^"]+)"')
_NEXT = re.compile(r'href="(\?page=\d+)"[^>]*title="Go to next page"')
_WHEN = re.compile(r"^\w+, (\w+ \d{1,2}, \d{4})(?:, (.+?))?(?: ET)?$")
_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", re.I)


class YCBAAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        names = CacheNamer(source.id)
        url, events = source.url, []
        for _ in range(MAX_PAGES):
            page = self.http.get_text(url, cache_name=names.next("html"))
            events += parse_grid(page, source.url)
            nxt = _NEXT.search(page)
            if not nxt:
                break
            url = urljoin(source.url, html.unescape(nxt.group(1)))
        start = datetime.combine(datetime.now(NEW_HAVEN).date(), time(), NEW_HAVEN)
        end = max((e.start for e in events), default=start)
        return FetchResult(events=[e for e in events if e.start >= start], window_start=start, window_end=end)


def _text(fragment: str | None) -> str | None:
    return clean_field(html.unescape(re.sub(r"<[^>]+>", " ", fragment))) if fragment else None


def parse_grid(page: str, base_url: str) -> list[RawEvent]:
    events = []
    for href, body in _CARD.findall(page):
        activity = _text(m.group(1)) if (m := _ACTIVITY.search(body)) else None
        title = _text(m.group(1)) if (m := _TITLE.search(body)) else None
        when = parse_when(_text(m.group(1)) or "") if (m := _DATE.search(body)) else None
        if not title or not when or (activity or "").lower() == "exhibition":
            continue
        start, end, all_day = when
        image = _IMAGE.search(body)
        events.append(
            RawEvent(
                source_event_id=f"{href}@{start.isoformat()}",
                title=title,
                start=start,
                end=end,
                all_day=all_day,
                url=urljoin(base_url, href),
                image_url=urljoin(base_url, image.group(1)) if image else None,
                tags=[activity] if activity else [],
            )
        )
    return events


def parse_when(text: str) -> tuple[datetime, datetime | None, bool] | None:
    """'Saturday, September 26, 2026, 10:30 am–12 pm ET' -> (start, end, all_day); None for ranges of days."""
    m = _WHEN.match(text.strip())
    if not m:
        return None
    day = datetime.strptime(m.group(1), "%B %d, %Y").date()
    if not m.group(2):
        return datetime.combine(day, time(), NEW_HAVEN), None, True
    parts = re.split(r"\s*[–-]\s*", m.group(2).replace("noon", "12 pm"))
    clocks = [_CLOCK.fullmatch(p.strip()) for p in parts]
    if not all(clocks) or len(clocks) > 2:
        return None
    end_meridiem = clocks[-1].group(3)
    if not end_meridiem:
        return None
    times = [_to_time(c, c.group(3) or end_meridiem) for c in clocks]
    if len(times) == 2 and not clocks[0].group(3) and times[0] > times[1]:
        times[0] = _to_time(clocks[0], "am")  # "11–1 pm": the start is in the morning
    start = datetime.combine(day, times[0], NEW_HAVEN)
    end = datetime.combine(day, times[1], NEW_HAVEN) if len(times) == 2 else None
    return start, end, False


def _to_time(m: re.Match, meridiem: str) -> time:
    hour = int(m.group(1)) % 12 + (12 if meridiem.lower() == "pm" else 0)
    return time(hour, int(m.group(2) or 0))
