"""Adapter for music.yale.edu (School of Music, plus ISM, the instrument collection, and Norfolk).

The events page loads rendered cards from /ajax/browse/get_items (jQuery "Load More"); one request
with a large `max` returns every upcoming event. Cards carry title, venue, series, and a date
without a year ("Sep 27, 3:00PM"); the list starts today and runs through the season, so the year
is inferred. A second request with the free-admission filter marks free events.
"""

import html
import json
import re
from datetime import date, datetime
from urllib.parse import urljoin

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.text import clean_field
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

API_PATH = "/ajax/browse/get_items"
MAX_ITEMS = 500

_ABOUT = re.compile(r'<article[^>]*\babout="([^"]+)"')
_FIELD = re.compile(r'field--name-field-([a-z_-]+)[^>]*>(.*?)</div>', re.S)
_DATE = re.compile(r'class="event-date">\s*(.*?)\s*</div>', re.S)
_TITLE = re.compile(r"<h2>(.*?)</h2>", re.S)
_LINK = re.compile(r'<a href="([^"]+)"[^>]*class="learn-more"[^>]*>\s*(.*?)\s*</a>', re.S)
_IMAGE = re.compile(r"background-image:\s*url\(([^)]+)\)")


class MusicAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        names = CacheNamer(source.id)
        url = urljoin(source.url, API_PATH)
        cards = self._items(url, {}, names)
        free = {_about(c) for c in self._items(url, {"free_admission": 1}, names)}
        today = datetime.now(NEW_HAVEN).date()
        events = [ev for c in cards if (ev := parse_card(c, source.url, today, _about(c) in free))]
        start = datetime.combine(today, datetime.min.time(), NEW_HAVEN)
        end = max((e.start for e in events), default=start)
        return FetchResult(events=events, window_start=start, window_end=end)

    def _items(self, url: str, filters: dict, names: CacheNamer) -> list[str]:
        params = {"search": "", "type": "event", "offset": "0", "max": str(MAX_ITEMS), "filters": json.dumps(filters)}
        return self.http.get_json(url, params=params, cache_name=names.next("json"))["items"]


def _about(card: str) -> str | None:
    m = _ABOUT.search(card)
    return m.group(1) if m else None


def parse_card(card: str, base_url: str, today: date, free: bool = False) -> RawEvent | None:
    card = re.sub(r"<!--.*?-->", "", card, flags=re.S)
    about, when, title = _about(card), _DATE.search(card), _TITLE.search(card)
    if not (about and when and title):
        return None
    start = infer_year(when.group(1), today)
    if start is None:
        return None
    fields = {k: clean_field(v) for k, v in _FIELD.findall(card)}
    link = _LINK.search(card)
    image = _IMAGE.search(card)
    series = fields.get("production")
    return RawEvent(
        source_event_id=about,
        title=clean_field(title.group(1)),
        description=series,
        start=start,
        location_name=fields.get("location"),
        url=urljoin(base_url, about),
        image_url=html.unescape(image.group(1)).strip("'\"") if image else None,
        tags=[series] if series else [],
        groups=["Yale School of Music"],
        free=True if free else None,
        cost="Tickets required" if link and link.group(2).strip().lower() == "tickets" and not free else None,
    )


def infer_year(text: str, today: date) -> datetime | None:
    """'Sep 27, 3:00PM' -> the next such date on or after yesterday (the list only shows upcoming events)."""
    for year in (today.year, today.year + 1):
        try:
            dt = datetime.strptime(f"{text.strip()} {year}", "%b %d, %I:%M%p %Y").replace(tzinfo=NEW_HAVEN)
        except ValueError:
            return None
        if dt.date() >= date.fromordinal(today.toordinal() - 1):
            return dt
    return None
