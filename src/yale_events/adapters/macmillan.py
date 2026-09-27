"""Adapter for macmillan.yale.edu/events (Drupal view, no feed).

The page holds two views: upcoming events (soonest first, 12 per page) and "Previous Events". Only
the first is read; its pager links carry `date_month_year=<today>&page=N`, followed until there is
no "next page" link. Teasers give the council/program, title, link, and date with start/end times,
but no venue (that's only on each event's page, which isn't fetched).
"""

import html
import re
from datetime import date, datetime, time
from urllib.parse import urljoin

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.text import clean_field
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

MAX_PAGES = 20
_PREVIOUS = re.compile(r'class="view view--block-all-previous')
_TEASER = re.compile(r'<article[^>]*class="node-teaser node-teaser--event.*?</article>', re.S)
_GROUP = re.compile(r'class="node-teaser__groups">(.*?)</div>', re.S)
_HEADING = re.compile(r'class="node-teaser__heading">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DATE = re.compile(r'field-node-event-date__(single-date|start-date|end-date)" datetime="(\d{4}-\d{2}-\d{2})')
_TIME = re.compile(r'field-node-event-date__(start-time|end-time)" datetime="(\d{1,2}:\d{2})')
_NEXT = re.compile(r'href="(\?[^"]*page=\d+)"[^>]*title="Go to next page"')


class MacMillanAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        names = CacheNamer(source.id)
        today = datetime.now(NEW_HAVEN).date()
        url: str | None = f"{source.url}?date_month_year={today.isoformat()}&page=0"
        events: list[RawEvent] = []
        for _ in range(MAX_PAGES):
            page = self.http.get_text(url, cache_name=names.next("html"))
            upcoming, next_link = upcoming_section(page)
            events += parse_teasers(upcoming, source.url)
            if not next_link:
                break
            url = urljoin(source.url, html.unescape(next_link))
        start = datetime.combine(today, time(), NEW_HAVEN)
        end = max((e.start for e in events), default=start)
        return FetchResult(events=[e for e in events if e.start >= start], window_start=start, window_end=end)


def upcoming_section(page: str) -> tuple[str, str | None]:
    """The upcoming view's HTML (everything before "Previous Events") and its next-page link."""
    m = _PREVIOUS.search(page)
    section = page[: m.start()] if m else page
    nxt = _NEXT.search(section)
    return section, nxt.group(1) if nxt else None


def parse_teasers(section: str, base_url: str) -> list[RawEvent]:
    return [ev for t in _TEASER.findall(section) if (ev := parse_teaser(t, base_url))]


def parse_teaser(teaser: str, base_url: str) -> RawEvent | None:
    heading = _HEADING.search(teaser)
    dates = dict(_DATE.findall(teaser))
    day = dates.get("single-date") or dates.get("start-date")
    if not (heading and day):
        return None
    times = dict(_TIME.findall(teaser))
    start_day = date.fromisoformat(day)
    end_day = date.fromisoformat(dates.get("end-date", day))
    all_day = "start-time" not in times
    start = datetime.combine(start_day, _clock(times.get("start-time")), NEW_HAVEN)
    end = None
    if "end-time" in times:
        end = datetime.combine(end_day, _clock(times["end-time"]), NEW_HAVEN)
    elif all_day and end_day != start_day:
        end = datetime.combine(end_day, time(), NEW_HAVEN)
    group = _GROUP.search(teaser)
    group_name = clean_field(html.unescape(re.sub(r"<[^>]+>", " ", group.group(1)))) if group else None
    path = heading.group(1)
    return RawEvent(
        source_event_id=f"{path}@{start.isoformat()}",
        title=clean_field(html.unescape(re.sub(r"<[^>]+>", "", heading.group(2)))),
        start=start,
        end=end,
        all_day=all_day,
        url=urljoin(base_url, path),
        groups=[group_name] if group_name else [],
    )


def _clock(hhmm: str | None) -> time:
    return time.fromisoformat(hhmm.zfill(5)) if hhmm else time()
