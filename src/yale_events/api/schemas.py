from datetime import date, datetime

from pydantic import BaseModel

from yale_events.adapters.ical import HIDDEN_LOCATION_LABEL
from yale_events.models import Event
from yale_events.normalize.location import LocationResolver, in_yale_bbox
from yale_events.normalize.online import split_online
from yale_events.normalize.time import NEW_HAVEN


class OnlineOut(BaseModel):
    url: str | None  # join or registration link
    details: str | None  # access text as the source gave it: meeting ID, passcode, instructions


class LocationOut(BaseModel):
    name: str | None  # the in-person venue; online access text is split out into `online`
    room: str | None
    address: str | None
    id: str | None  # canonical building, see /locations
    building: str | None
    area: str | None  # see /areas
    lat: float | None
    lon: float | None
    virtual: bool
    online: OnlineOut | None = None  # how to join, when the source gave more than "Online"
    sign_in_url: str | None = None  # the venue is shown only to signed-in Yale users on this page (Yale Connect)


class ListingOut(BaseModel):
    source: str
    url: str | None


class EventOut(BaseModel):
    id: str
    title: str
    description: str | None
    start: datetime  # New Haven local time with offset
    end: datetime | None
    all_day: bool
    location: LocationOut
    categories: list[str]
    free_food: bool
    free: bool | None
    cost: str | None
    cancelled: bool
    ongoing: bool
    url: str | None
    image_url: str | None
    audience: list[str]
    groups: list[str]
    source: str
    series_id: str | None
    series_last_date: date | None  # a recurring series' last date per its source (an exhibition's closing day)
    also_listed_by: list[ListingOut]  # other sources carrying the same event (merged by dedupe)
    updated_at: datetime
    added_at: datetime  # when this listing first appeared in a scrape (sort=added orders by it)

    @classmethod
    def from_event(cls, e: Event, locations: LocationResolver, duplicates: list[Event] = ()) -> "EventOut":
        b = locations.buildings.get(e.location_id) if e.location_id else None
        # Source geocodes are sometimes wildly off; prefer the building's coordinates, drop implausible ones.
        if b and b.lat is not None:
            lat, lon = b.lat, b.lon
        elif in_yale_bbox(e.lat, e.lon) or e.area == "off-campus":
            lat, lon = e.lat, e.lon
        else:
            lat, lon = None, None
        venue = split_online(e.location_name)
        return cls(
            id=e.id,
            title=e.title,
            description=e.description,
            start=e.start.astimezone(NEW_HAVEN),
            end=e.end.astimezone(NEW_HAVEN) if e.end else None,
            all_day=e.all_day,
            location=LocationOut(
                name=venue.venue,
                room=e.room,
                address=e.address,
                id=e.location_id,
                building=b.name if b else None,
                area=e.area,
                lat=lat,
                lon=lon,
                virtual=e.virtual or venue.online,
                online=OnlineOut(url=venue.url, details=venue.details) if venue.url or venue.details else None,
                sign_in_url=e.url if e.location_name == HIDDEN_LOCATION_LABEL else None,
            ),
            categories=list(e.categories),
            free_food=e.free_food,
            free=e.free,
            cost=e.cost,
            cancelled=e.cancelled,
            ongoing=e.ongoing,
            url=e.url,
            image_url=e.image_url,
            audience=e.audience or [],
            groups=e.groups or [],
            source=e.source_id,
            series_id=e.series_id,
            series_last_date=e.series_last_date,
            also_listed_by=[ListingOut(source=d.source_id, url=d.url) for d in duplicates],
            updated_at=e.updated_at,
            added_at=e.first_seen,
        )


class EventPage(BaseModel):
    events: list[EventOut]
    next_cursor: str | None  # pass as ?cursor= to get the next page; null on the last page


class DayOut(BaseModel):
    date: date
    events: int  # events starting that day (New Haven time)


class CategoryOut(BaseModel):
    id: str
    name: str
    upcoming: int


class AreaOut(BaseModel):
    id: str
    name: str
    upcoming: int


class OrgOut(BaseModel):
    id: str
    name: str
    kind: str  # college | school | department | organization
    upcoming: int


class BuildingOut(BaseModel):
    id: str
    name: str
    area: str
    lat: float | None
    lon: float | None
    upcoming: int


class SourceOut(BaseModel):
    id: str
    name: str
    type: str
    url: str
    enabled: bool
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_error: str | None
    upcoming: int
