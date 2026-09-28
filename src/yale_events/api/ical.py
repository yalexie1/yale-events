from collections.abc import Iterable
from datetime import timedelta

from icalendar import Calendar, Event as VEvent, vDuration, vGeo

from yale_events.adapters.ical import HIDDEN_LOCATION_LABEL
from yale_events.models import Event
from yale_events.normalize.location import in_yale_bbox
from yale_events.normalize.time import NEW_HAVEN

UID_DOMAIN = "yale-events.local"
REFRESH = timedelta(hours=4)  # matches the scrape schedule


def to_ics(events: Iterable[Event], name: str) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//yale-events//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-timezone", "America/New_York")
    cal.add("refresh-interval", REFRESH, parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", vDuration(REFRESH))
    for e in events:
        cal.add_component(to_vevent(e))
    return cal.to_ical()


def to_vevent(e: Event) -> VEvent:
    v = VEvent()
    v.add("uid", f"{e.id}@{UID_DOMAIN}")
    v.add("dtstamp", e.updated_at)
    v.add("last-modified", e.updated_at)
    v.add("summary", e.title)
    if e.all_day:
        day = e.start.astimezone(NEW_HAVEN).date()
        v.add("dtstart", day)
        v.add("dtend", day + timedelta(days=1))
    else:
        # UTC times need no VTIMEZONE block and every client converts them correctly.
        v.add("dtstart", e.start)
        if e.end:
            v.add("dtend", e.end)
    if location := format_location(e):
        v.add("location", location)
    if e.lat is not None and e.lon is not None and in_yale_bbox(e.lat, e.lon):
        v.add("geo", vGeo((e.lat, e.lon)))
    if e.location_name == HIDDEN_LOCATION_LABEL and e.url:  # the room is behind a Yale Connect sign-in
        description = "\n\n".join(p for p in [f"Location: sign in at {e.url}", e.description] if p)
    else:
        description = "\n\n".join(p for p in [e.description, e.url] if p)
    if description:
        v.add("description", description)
    if e.url:
        v.add("url", e.url)
    if categories := list(e.categories):
        v.add("categories", categories)
    v.add("status", "CANCELLED" if e.cancelled else "CONFIRMED")
    return v


def format_location(e: Event) -> str | None:
    if e.virtual and not e.location_name:
        return "Online"
    parts = [e.location_name]
    if e.room and e.room not in (e.location_name or ""):
        parts.insert(0, e.room)
    return ", ".join(p for p in parts if p) or None
