"""Adapter for pages that embed schema.org Event objects as JSON-LD (e.g. architecture.yale.edu/calendar).

Reads every <script type="application/ld+json"> on the listing page, keeping objects (or @graph
members) whose @type is Event or a subtype. Naive startDate/endDate values are New Haven time.
Past events on the page are dropped. When an event has no `url`, a link on the page whose slug
matches the event name is used.

Options:
  days: how far ahead to keep events (default 365)
"""

import json
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import urljoin

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.text import clean_description, clean_field
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

DEFAULT_DAYS = 365
_SCRIPT = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
_HREF = re.compile(r'href="([^"#]+)"')


class JsonLdAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        page = self.http.get_text(source.url, cache_name=CacheNamer(source.id).next("html"))
        start = datetime.combine(datetime.now(NEW_HAVEN).date(), time(), NEW_HAVEN)
        end = start + timedelta(days=int(source.options.get("days", DEFAULT_DAYS)))
        events = [e for e in parse_page(page, source.url) if start <= e.start < end]
        return FetchResult(events=events, window_start=start, window_end=end)


def parse_page(page: str, page_url: str) -> list[RawEvent]:
    links = [urljoin(page_url, h) for h in _HREF.findall(page)]
    events, seen = [], set()
    for obj in _event_objects(page):
        ev = parse_event(obj, page_url, links)
        if ev and ev.source_event_id not in seen:
            seen.add(ev.source_event_id)
            events.append(ev)
    return events


def _event_objects(page: str):
    for raw in _SCRIPT.findall(page):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop(0)
            if not isinstance(obj, dict):
                continue
            stack += obj.get("@graph", [])
            types = obj.get("@type")
            if any(str(t).endswith("Event") for t in (types if isinstance(types, list) else [types])):
                yield obj


def parse_event(obj: dict, page_url: str, links: list[str]) -> RawEvent | None:
    title = clean_field(obj.get("name"))
    if not title or not obj.get("startDate"):
        return None
    start, all_day = _parse_when(obj["startDate"])
    end = _parse_when(obj["endDate"])[0] if obj.get("endDate") and not all_day else None

    place = obj.get("location") or {}
    if isinstance(place, list):
        place = place[0] if place else {}
    address = place.get("address") if isinstance(place, dict) else None
    if isinstance(address, dict):
        address = ", ".join(str(address[k]) for k in ("streetAddress", "addressLocality") if address.get(k))
    location = clean_field(place.get("name")) if isinstance(place, dict) else clean_field(str(place))
    geo = place.get("geo") or {} if isinstance(place, dict) else {}

    url = obj.get("url") or _link_for(title, links)
    image = obj.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    return RawEvent(
        source_event_id=f"{url or title}@{start.isoformat()}",
        title=title,
        description=clean_description(obj.get("description")),
        start=start,
        end=end,
        all_day=all_day,
        location_name=location,
        address=clean_field(address),
        lat=_float(geo.get("latitude")),
        lon=_float(geo.get("longitude")),
        virtual=(location or "").lower() in {"online", "virtual", "zoom"}
        or "OnlineEventAttendanceMode" in str(obj.get("eventAttendanceMode", "")),
        url=urljoin(page_url, url) if url else None,
        image_url=image,
        cancelled="EventCancelled" in str(obj.get("eventStatus", "")),
    )


def _parse_when(value: str) -> tuple[datetime, bool]:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return datetime.combine(date.fromisoformat(value.strip()), time(), NEW_HAVEN), True
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=NEW_HAVEN)), False


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _link_for(title: str, links: list[str]) -> str | None:
    slug = _slug(title)
    return next((l for l in links if slug and l.rstrip("/").endswith(slug)), None)


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
