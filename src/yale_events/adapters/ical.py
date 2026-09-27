"""Adapter for any iCalendar (.ics) feed: school calendars, athletics, public Google Calendars.

Options:
  days: how far ahead to keep occurrences (default 90)
  location_contains: keep only events whose LOCATION contains this text (e.g. home games)
  title_strip: regex removed from titles (e.g. "^Yale University ")
  exclude_title: skip events whose title matches this regex (e.g. registrar deadlines)
  exclude_location: skip events whose LOCATION matches this regex (e.g. "Sign in to download")
  exclude_tags: skip events with a CATEGORIES value matching this regex (whole value, case-insensitive)
  description_strip: regex removed from descriptions (e.g. a trailing "Event Details: <url>" footer)
  hidden_location: regex for a LOCATION that hides the venue from anonymous readers (Yale Connect's
    "Sign in to download the location"). Such events are kept only if a CATEGORIES value fully matches
    `hidden_keep_tags` and the title doesn't match `hidden_exclude_title`; their location becomes
    `hidden_location_label`.

CampusGroups feeds (Yale Connect) label each CATEGORIES line with X-CG-CATEGORY: `event_type` values
contain commas ("Lecture, Talk, or Panel") and are kept whole, `club_acronym` is an internal code and
is dropped. ORGANIZER's CN becomes the event's group.
"""

import re
from datetime import date, datetime, time, timedelta

import recurring_ical_events
from icalendar import Calendar

from yale_events.adapters.base import CacheNamer, PoliteClient
from yale_events.adapters.text import clean_description, clean_field
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN
from yale_events.schemas import FetchResult, RawEvent

DEFAULT_DAYS = 90
HIDDEN_LOCATION_LABEL = "Location on Yale Connect (sign in)"


class ICalAdapter:
    def __init__(self, http: PoliteClient):
        self.http = http

    def fetch(self, source: SourceConfig) -> FetchResult:
        today = datetime.now(NEW_HAVEN).date()
        start = datetime.combine(today, time(), NEW_HAVEN)
        end = start + timedelta(days=int(source.options.get("days", DEFAULT_DAYS)))
        data = self.http.get_text(source.url, cache_name=CacheNamer(source.id).next("ics"))
        return FetchResult(events=parse_calendar(data, start, end, source.options), window_start=start, window_end=end)


def parse_calendar(data: str | bytes, start: datetime, end: datetime, options: dict | None = None) -> list[RawEvent]:
    options = options or {}
    cal = Calendar.from_ical(data)
    # Occurrences of recurring events share a UID; they get the original start appended to stay unique.
    recurring = {
        str(c["UID"]) for c in cal.walk("VEVENT") if {"RRULE", "RDATE", "RECURRENCE-ID"} & set(c.keys())
    }
    occurrences = [
        o for o in recurring_ical_events.of(cal).between(start, end)
        if str(o.get("CLASS", "PUBLIC")).upper() == "PUBLIC"
    ]  # fmt: skip
    series_dates: dict[str, list[date]] = {}
    for o in occurrences:
        series_dates.setdefault(str(o["UID"]), []).append(_as_date(o["DTSTART"].dt))

    location_filter = (options.get("location_contains") or "").lower()
    title_strip = re.compile(options["title_strip"]) if options.get("title_strip") else None
    exclude = _regex(options.get("exclude_title"))
    exclude_location = _regex(options.get("exclude_location"))
    exclude_tags = _regex(options.get("exclude_tags"))
    description_strip = _regex(options.get("description_strip"))
    hidden = _regex(options.get("hidden_location"))
    hidden_keep_tags = _regex(options.get("hidden_keep_tags"))
    hidden_exclude_title = _regex(options.get("hidden_exclude_title"))
    events = []
    for o in occurrences:
        location = clean_field(o.get("LOCATION"))
        if location_filter and location_filter not in (location or "").lower():
            continue
        if exclude_location and location and exclude_location.search(location):
            continue
        is_hidden = bool(hidden and location and hidden.search(location))
        if is_hidden:
            location = options.get("hidden_location_label", HIDDEN_LOCATION_LABEL)
        ev = parse_occurrence(o, location, recurring, series_dates, title_strip)
        if ev is None or (exclude and exclude.search(ev.title)):
            continue
        if exclude_tags and any(exclude_tags.fullmatch(t) for t in ev.tags):
            continue
        if is_hidden and not (
            hidden_keep_tags and any(hidden_keep_tags.fullmatch(t) for t in ev.tags)
            and not (hidden_exclude_title and hidden_exclude_title.search(ev.title))
        ):
            continue
        if description_strip and ev.description:
            ev.description = description_strip.sub("", ev.description).strip() or None
        events.append(ev)
    return events


def _regex(pattern: str | None) -> re.Pattern | None:
    return re.compile(pattern, re.IGNORECASE) if pattern else None


def _tags(o) -> list[str]:
    props = o.get("CATEGORIES")
    tags: list[str] = []
    for prop in props if isinstance(props, list) else [props] if props is not None else []:
        values = [str(c).strip() for c in getattr(prop, "cats", [])]
        kind = prop.params.get("X-CG-CATEGORY") if hasattr(prop, "params") else None
        if kind == "club_acronym":
            continue
        if kind == "event_type":
            values = [", ".join(values)]
        tags += [v for v in values if v and v not in tags]
    return tags


def _organizer(o) -> list[str]:
    org = o.get("ORGANIZER")
    name = clean_field(org.params.get("CN")) if org is not None and hasattr(org, "params") else None
    return [name] if name else []


def parse_occurrence(o, location, recurring, series_dates, title_strip) -> RawEvent | None:
    uid = str(o["UID"])
    title = clean_field(o.get("SUMMARY"))
    if title and title_strip:
        title = title_strip.sub("", title).strip()
    if not title:
        return None

    dtstart = o["DTSTART"].dt
    all_day = not isinstance(dtstart, datetime)
    start = datetime.combine(dtstart, time(), NEW_HAVEN) if all_day else dtstart
    end = None
    if not all_day:
        if "DTEND" in o:
            end = o["DTEND"].dt
        elif "DURATION" in o:
            end = start + o["DURATION"].dt

    event_id = uid
    series = None
    if uid in recurring:
        original = o["RECURRENCE-ID"].dt if "RECURRENCE-ID" in o else dtstart
        event_id = f"{uid}/{original.isoformat()}"
        dates = series_dates[uid]
        series = (uid, min(dates), max(dates))

    geo = o.get("GEO")
    return RawEvent(
        source_event_id=event_id,
        title=title,
        description=clean_description(o.get("DESCRIPTION")),
        start=start,
        end=end,
        all_day=all_day,
        location_name=location,
        lat=geo.latitude if geo else None,
        lon=geo.longitude if geo else None,
        virtual=bool(location and re.match(r"(?i)(online|zoom|virtual)\b", location)),
        url=str(o["URL"]) if o.get("URL") else None,
        tags=_tags(o),
        groups=_organizer(o),
        cancelled=str(o.get("STATUS", "")).upper() == "CANCELLED",
        source_updated_at=o["LAST-MODIFIED"].dt if o.get("LAST-MODIFIED") else None,
        series_id=series[0] if series else None,
        series_first_date=series[1] if series else None,
        series_last_date=series[2] if series else None,
    )


def _as_date(d: date | datetime) -> date:
    return d.astimezone(NEW_HAVEN).date() if isinstance(d, datetime) and d.tzinfo else (d.date() if isinstance(d, datetime) else d)
