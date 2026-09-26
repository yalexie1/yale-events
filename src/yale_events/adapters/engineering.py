"""Adapter for engineering.yale.edu (Concrete CMS).

The events page loads its list by POSTing a form to a JSON endpoint ({events, showMore}); each event's
page holds the location and description, which the list lacks.
"""

import re
from datetime import datetime, time, timedelta

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.text import clean_description, clean_field
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

PAGE_SIZE = 50
MAX_PAGES = 10
WINDOW_DAYS = 365  # the endpoint lists every upcoming event


class EngineeringAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        names = CacheNamer(source.id)
        items, page = [], 1
        while page <= MAX_PAGES:
            form = {"department": "", "onlyalldepartments": "false", "template": "listing",
                    "max": str(PAGE_SIZE), "currentPage": str(page)}  # fmt: skip
            data = self.http.post_json(source.url, form, cache_name=names.next("json"))
            items += data.get("events") or []
            if not data.get("showMore"):
                break
            page += 1

        events = []
        for item in items:
            detail = self.http.get_text(item["link"], cache_name=names.next("html")) if item.get("link") else ""
            if (ev := parse_item(item, detail)) is not None:
                events.append(ev)
        start = datetime.combine(datetime.now(NEW_HAVEN).date(), time(), NEW_HAVEN)
        return FetchResult(events=events, window_start=start, window_end=start + timedelta(days=WINDOW_DAYS))


def _detail_field(html: str, label: str) -> str | None:
    m = re.search(rf"<h3[^>]*>\s*{label}\s*</h3>\s*<p>(.*?)</p>", html, re.S | re.I)
    return clean_field(m.group(1)) if m else None


def _description(html: str) -> str | None:
    # Body copy is the column that ends with the "Add to Calendar" export link (the details panel
    # above it uses the same column classes).
    export = html.find("/ccm/calendar/event/export")
    column = html.rfind('lg:w-7/12 pt-8">', 0, export)
    if export < 0 or column < 0:
        return None
    body = html[column + len('lg:w-7/12 pt-8">') : export]
    return clean_description(body[: body.rfind('<div class="py-8">')])


def _parse_time(day: datetime, s: str | None) -> datetime | None:
    if not s or not s.strip():
        return None
    return datetime.combine(day.date(), datetime.strptime(s.strip().upper(), "%I:%M %p").time(), NEW_HAVEN)


def parse_item(item: dict, detail_html: str = "") -> RawEvent | None:
    try:
        day = datetime.strptime(item["date"].strip(), "%B %d, %Y")
    except (KeyError, ValueError):
        return None
    start = _parse_time(day, item.get("startTime"))
    end = _parse_time(day, item.get("endTime"))
    departments = [d["name"] if isinstance(d, dict) else d for d in item.get("departments") or []]
    return RawEvent(
        source_event_id=str(item["id"]),
        title=clean_field(item["title"]) or "Untitled",
        description=_description(detail_html),
        start=start or datetime.combine(day.date(), time(), NEW_HAVEN),
        end=end if start else None,
        all_day=start is None,
        location_name=_detail_field(detail_html, "Location"),
        url=item.get("link"),
        groups=[d for d in departments if d != "All Departments"],
    )
