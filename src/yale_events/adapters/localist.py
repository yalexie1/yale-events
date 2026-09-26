"""Adapter for Localist calendars (events.yale.edu). API docs: https://developer.localist.com/doc/api"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from yale_events.adapters.base import PoliteClient
from yale_events.config import SourceConfig
from yale_events.schemas import FetchResult, RawEvent

NEW_HAVEN = ZoneInfo("America/New_York")
PAGE_SIZE = 100  # Localist maximum
MAX_PAGES = 50  # safety cap: 5,000 occurrences


class LocalistAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        days = int(source.options.get("days", 60))
        start_date = datetime.now(NEW_HAVEN).date()
        params = {"start": start_date.isoformat(), "days": days, "pp": PAGE_SIZE}
        for key in ("group_id", "type"):  # optional Localist filters, e.g. to scope to one department
            if key in source.options:
                params[key] = source.options[key]

        events: list[RawEvent] = []
        page = 1
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        while page and page <= MAX_PAGES:
            data = self.http.get_json(
                f"{source.url.rstrip('/')}/api/2/events",
                {**params, "page": page},
                cache_name=f"{source.id}-{stamp}-p{page}.json",
            )
            events.extend(parse_event(w["event"]) for w in data.get("events", []))
            page = data.get("page", {}).get("next_page")

        return FetchResult(
            events=events,
            window_start=_local_midnight(start_date),
            window_end=_local_midnight(start_date + timedelta(days=days)),
        )


def _local_midnight(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), NEW_HAVEN)


def _names(filters: dict, key: str) -> list[str]:
    return [f["name"] for f in filters.get(key) or []]


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clean(s: str | None) -> str | None:
    s = (s or "").strip()
    return s or None


def parse_event(e: dict) -> RawEvent:
    """Parse one entry of Localist's /events response. Each entry carries exactly one occurrence."""
    inst = e["event_instances"][0]["event_instance"]
    filters = e.get("filters") or {}
    geo = e.get("geo") or {}
    return RawEvent(
        # Instance id is unique per occurrence, so recurring events become one row per date.
        source_event_id=str(inst["id"]),
        title=e["title"].strip(),
        description=_clean(e.get("description_text")),
        start=datetime.fromisoformat(inst["start"]),
        end=datetime.fromisoformat(inst["end"]) if inst.get("end") else None,
        all_day=bool(inst.get("all_day")),
        location_name=_clean(e.get("location_name")) or _clean(e.get("location")),
        room=_clean(e.get("room_number")),
        address=_clean(e.get("address")),
        lat=_float(geo.get("latitude")),
        lon=_float(geo.get("longitude")),
        virtual=e.get("experience") == "virtual",
        url=e.get("localist_url") or e.get("url"),
        image_url=e.get("photo_url"),
        tags=_names(filters, "event_types") + _names(filters, "event_topics"),
        keywords=list(dict.fromkeys((e.get("tags") or []) + (e.get("keywords") or []))),
        audience=_names(filters, "event_audience"),
        groups=[g["name"] for g in e.get("groups") or []],
        free=e.get("free"),
        cost=_clean(e.get("ticket_cost")),
        cancelled=e.get("status") == "cancelled",
        source_updated_at=datetime.fromisoformat(e["updated_at"]) if e.get("updated_at") else None,
    )
