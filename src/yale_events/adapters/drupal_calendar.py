"""Adapter for Yale's older Drupal 7 calendar pages (oiss.yale.edu/calendar, nursing.yale.edu/calendar).

The "All Upcoming" view lists `views-row` cards whose dates carry exact ISO datetimes in
`content` attributes (date-display-start/-end, or date-display-single for one point in time).
No feed is available (the Date iCal views aren't enabled).
"""

import re
from datetime import datetime
from urllib.parse import urljoin

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.text import clean_description, clean_field
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

_ROW = re.compile(r'<div class="views-row[ "].*?(?=<div class="views-row[ "]|</div>\s*</div>\s*<div class="view-footer|$)', re.S)
_TITLE = re.compile(r"<h3[^>]*>\s*<a href=\"?([^\" >]+)\"?[^>]*>(.*?)</a>", re.S)
_START = re.compile(r'class="date-display-(?:start|single)"[^>]*content="([^"]+)"')
_END = re.compile(r'class="date-display-end"[^>]*content="([^"]+)"')
_DESC = re.compile(r"</h3>.*?<p>(.*?)</p>", re.S)
_IMAGE = re.compile(r'<img[^>]+src="([^"]+)"')
_LOCATION = re.compile(r'class="[^"]*(?:event-location|field-name-field-location)[^"]*"[^>]*>(.*?)</(?:div|span)>', re.S)


class DrupalCalendarAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        page = self.http.get_text(source.url, cache_name=CacheNamer(source.id).next("html"))
        events = parse_calendar_page(page, source.url)
        start = datetime.now(NEW_HAVEN).replace(hour=0, minute=0, second=0, microsecond=0)
        end = max((e.start for e in events), default=start)
        return FetchResult(events=[e for e in events if e.start >= start], window_start=start, window_end=end)


def parse_calendar_page(page: str, base_url: str) -> list[RawEvent]:
    content = page[page.find('class="view-content"') :] if 'class="view-content"' in page else ""
    events = []
    for row in _ROW.findall(content):
        title, start = _TITLE.search(row), _START.search(row)
        if not (title and start):
            continue
        end = _END.search(row)
        url = urljoin(base_url, title.group(1))
        start_dt = datetime.fromisoformat(start.group(1))
        location = _LOCATION.search(row)
        image = _IMAGE.search(row)
        events.append(
            RawEvent(
                # The link is the event's own page (often a Yale Connect RSVP); unique per event.
                source_event_id=f"{url}@{start_dt.isoformat()}",
                title=clean_field(title.group(2)),
                description=clean_description(_DESC.search(row).group(1)) if _DESC.search(row) else None,
                start=start_dt,
                end=datetime.fromisoformat(end.group(1)) if end else None,
                location_name=clean_field(location.group(1)) if location else None,
                url=url,
                image_url=urljoin(base_url, image.group(1)) if image else None,
            )
        )
    return events
