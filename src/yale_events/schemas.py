from datetime import date, datetime

from pydantic import BaseModel, Field


class RawEvent(BaseModel):
    """One occurrence of an event, as extracted by an adapter before normalization."""

    source_event_id: str
    title: str
    description: str | None = None
    start: datetime
    end: datetime | None = None
    all_day: bool = False

    location_name: str | None = None
    room: str | None = None
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    virtual: bool = False

    url: str | None = None
    image_url: str | None = None

    # Source-provided taxonomy (e.g. Localist event types + topics); mapped to our categories later.
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)

    free: bool | None = None
    cost: str | None = None
    cancelled: bool = False
    source_updated_at: datetime | None = None

    # Recurring series this occurrence belongs to (e.g. a months-long exhibition listed daily).
    series_id: str | None = None
    series_first_date: date | None = None
    series_last_date: date | None = None
    ongoing: bool = False  # a long span with no known session dates; keeps its end however far off


class FetchResult(BaseModel):
    """Events from one source plus the time window they cover, used to detect removed events."""

    events: list[RawEvent]
    window_start: datetime
    window_end: datetime
