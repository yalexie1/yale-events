"""Adapter for medicine.yale.edu (School of Medicine, incl. Public Health and affiliated departments).

The calendar page is a React app; its "next page" button calls a JSON endpoint under
/website-api-data/, which takes a date range and page size. Its iCal export
(/event/feed-organization-events-download/) times out, even for a week.

Options:
  organization_id: root organization whose events (and its sub-organizations') to list (default 113592, YSM)
  days: how far ahead to fetch (default 90)
  audiences: which audience values to keep (default Everyone, YaleOnly; the others are internal to a department)
"""

from datetime import datetime, time, timedelta
from urllib.parse import urljoin

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.text import clean_description, clean_field
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

API_PATH = "/website-api-data/ysm/feed-event-list/"
DEFAULT_ORG = 113592
DEFAULT_DAYS = 90
PAGE_SIZE = 200
MAX_PAGES = 20
AUDIENCE_NAMES = {"Everyone": "General Public", "YaleOnly": "Yale Community"}


class YSMAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        opts = source.options
        start = datetime.combine(datetime.now(NEW_HAVEN).date(), time(), NEW_HAVEN)
        end = start + timedelta(days=int(opts.get("days", DEFAULT_DAYS)))
        audiences = set(opts.get("audiences", AUDIENCE_NAMES))
        names = CacheNamer(source.id)
        items: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            data = self.http.get_json(
                urljoin(source.url, API_PATH),
                params={
                    "isPastDirection": "false",
                    "organizationId": str(opts.get("organization_id", DEFAULT_ORG)),
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "pageNumber": str(page),
                    "pageSize": str(PAGE_SIZE),
                },
                cache_name=names.next("json"),
            )
            items += data["collection"]
            if not data["collection"] or len(items) >= data["totalItemCount"]:
                break
        events = [parse_item(e, source.url) for e in items if e.get("audience") in audiences]
        return FetchResult(events=events, window_start=start, window_end=end)


def parse_item(e: dict, base_url: str) -> RawEvent:
    all_day = bool(e["isAllDay"])
    start = datetime.fromisoformat(e["startDate"])
    end = datetime.fromisoformat(e["endDate"]) if e.get("endDate") else None
    if all_day:
        start, end = datetime.combine(start.astimezone(NEW_HAVEN).date(), time(), NEW_HAVEN), None

    loc = e.get("eventLocation") or {}
    online = e.get("virtualLocation") or {}
    geo = loc.get("geoPoint") or {}
    address = clean_field(loc.get("streetAddress"))
    location_name = clean_field(loc.get("building")) or address
    virtual = not loc and bool(online.get("url") or online.get("description"))

    speakers = ", ".join(s["name"] for s in e.get("speakers") or [] if s.get("name"))
    parts = [
        clean_field(e.get("subTitle")),
        f"Speakers: {speakers}" if speakers else None,
        clean_description(e.get("description")),
        f"Online: {online['url']}" if online.get("url") else None,
    ]
    image = (e.get("thumbnailImage") or {}).get("url")
    return RawEvent(
        source_event_id=str(e["id"]),
        title=clean_field(e["title"]),
        description="\n\n".join(p for p in parts if p) or None,
        start=start,
        end=end,
        all_day=all_day,
        location_name=location_name or (clean_field(online.get("description")) if virtual else None),
        address=address,
        lat=geo.get("latitude"),
        lon=geo.get("longitude"),
        virtual=virtual,
        url=urljoin(base_url, e["detailsEventUrl"]) if e.get("detailsEventUrl") else None,
        image_url=image,
        audience=[AUDIENCE_NAMES.get(e.get("audience"), e.get("audience"))] if e.get("audience") else [],
        groups=["Yale School of Medicine"],
        cancelled=e.get("status") == "Cancelled",
    )
