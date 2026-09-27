"""Adapter for Tsai CITY (Tsai Center for Innovative Thinking at Yale).

city.yale.edu/events lists Tsai CITY's events, and each links to its registration page on Luma. The
Luma calendar behind them publishes a public iCal feed (`url`), which has exact times, stable UIDs,
and coordinates but no description or URL: DESCRIPTION is only "Get up-to-date information at:
<luma link>", an address, and "Hosted by ...". So the feed is read with the ical adapter, the Luma
link becomes the event URL, and the blurb and image come from the city.yale.edu listing
(`options.listing`, one request), matched by that link.

Items on the listing that aren't on Luma are mostly application deadlines (Airtable forms) and are
not added.
"""

import html
import re
from datetime import datetime, time, timedelta
from urllib.parse import urljoin

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.ical import DEFAULT_DAYS, parse_calendar
from yale_events.adapters.text import html_to_text
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

_LUMA_LINK = re.compile(r"Get up-to-date information at:\s*(https?://\S+)")
_TEASER = re.compile(r'<article class="[^"]*node-teaser--event.*?</article>', re.S)
_HREF = re.compile(r'node-teaser__heading">\s*<a href="([^"]+)"')
_BODY = re.compile(r'node-teaser__body">(.*?)</div>', re.S)
_IMAGE = re.compile(r'<img[^>]+src="([^"]+)"')


class TsaiCityAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        names = CacheNamer(source.id)
        start = datetime.combine(datetime.now(NEW_HAVEN).date(), time(), NEW_HAVEN)
        end = start + timedelta(days=int(source.options.get("days", DEFAULT_DAYS)))
        feed = self.http.get_text(source.url, cache_name=names.next("ics"))
        events = [from_luma(e) for e in parse_calendar(feed, start, end, source.options)]
        if listing_url := source.options.get("listing"):
            teasers = parse_listing(self.http.get_text(listing_url, cache_name=names.next("html")), listing_url)
            events = [with_teaser(e, teasers.get(_key(e.url))) for e in events]
        return FetchResult(events=events, window_start=start, window_end=end)


def _key(url: str | None) -> str | None:
    return url.rstrip("/").split("//", 1)[-1].lower() if url else None


def from_luma(ev: RawEvent) -> RawEvent:
    """Use the Luma link as the URL and drop the boilerplate description (link, address, hosts)."""
    link = _LUMA_LINK.search(ev.description or "")
    return ev.model_copy(update={"url": ev.url or (link.group(1) if link else None), "description": None})


def parse_listing(page: str, base_url: str) -> dict[str, dict]:
    """Teasers on city.yale.edu/events keyed by their link: {"description", "image_url"}."""
    teasers = {}
    for block in _TEASER.findall(page):
        href = _HREF.search(block)
        if not href:
            continue
        body = _BODY.search(block)
        image = _IMAGE.search(block)
        teasers[_key(urljoin(base_url, html.unescape(href.group(1))))] = {
            "description": html_to_text(body.group(1)) if body else None,
            "image_url": urljoin(base_url, html.unescape(image.group(1))) if image else None,
        }
    return teasers


def with_teaser(ev: RawEvent, teaser: dict | None) -> RawEvent:
    if not teaser:
        return ev
    return ev.model_copy(update={k: v for k, v in teaser.items() if v and not getattr(ev, k)})
