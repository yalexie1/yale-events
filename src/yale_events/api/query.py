"""Filters shared by the JSON feed and the iCal feed."""

import base64
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy import Select, and_, false, func, or_, select, true
from sqlalchemy.orm import aliased

from yale_events.models import Event, EventCategory
from yale_events.normalize import default_normalizer
from yale_events.normalize.time import NEW_HAVEN
from yale_events.orgs import Org, default_orgs


@dataclass
class EventFilters:
    start: datetime | None = None
    end: datetime | None = None
    category: list[str] = field(default_factory=list)
    area: list[str] = field(default_factory=list)
    location: list[str] = field(default_factory=list)
    source: list[str] = field(default_factory=list)
    org: list[str] = field(default_factory=list)  # see organizations.yaml
    q: str | None = None
    free_food: bool | None = None
    include_ongoing: bool = False
    include_cancelled: bool = False


def parse_when(value: str | None, name: str, *, end: bool = False) -> datetime | None:
    """'2026-10-01' -> local midnight (the day after for `end`, so end dates are inclusive).
    Datetimes without an offset are New Haven time."""
    if value is None:
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        pass
    else:
        return datetime.combine(d + timedelta(days=1) if end else d, time(), NEW_HAVEN)
    # An unencoded "+" in a query string arrives as a space: "10:00:00 00:00" -> "10:00:00+00:00".
    value = re.sub(r" (\d{2}:?\d{2})$", r"+\1", value)
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{name}: expected YYYY-MM-DD or an ISO 8601 datetime, got {value!r}") from None
    return dt if dt.tzinfo else dt.replace(tzinfo=NEW_HAVEN)


def split_values(values: list[str] | None) -> list[str]:
    """Accept both ?category=a&category=b and ?category=a,b."""
    return [v.strip() for raw in values or [] for v in raw.split(",") if v.strip()]


def validate(f: EventFilters) -> None:
    n = default_normalizer()
    for name, given, known in [
        ("category", f.category, n.categorizer.categories),
        ("area", f.area, n.locations.areas),
        ("location", f.location, n.locations.buildings),
        ("org", f.org, default_orgs()),
    ]:
        if unknown := [v for v in given if v not in known]:
            raise HTTPException(422, f"unknown {name}: {', '.join(unknown)} (see /{name_to_path(name)})")
    if f.start and f.end and f.end <= f.start:
        raise HTTPException(422, "end must be after start")


def name_to_path(name: str) -> str:
    return {"category": "categories", "area": "areas", "location": "locations", "org": "orgs"}[name]


def build_query(f: EventFilters) -> Select[tuple[Event]]:
    stmt = select(Event).where(Event.stale.is_(False), Event.duplicate_of.is_(None))
    if f.start:
        # Overlap, not just "starts after": keep events already in progress, including today's all-day ones.
        stmt = stmt.where(
            or_(
                Event.start >= f.start,
                Event.end > f.start,
                and_(Event.all_day, Event.start > f.start - timedelta(days=1)),
            )
        )
    if f.end:
        stmt = stmt.where(Event.start < f.end)
    if f.category:
        stmt = stmt.where(
            Event.id.in_(select(EventCategory.event_id).where(EventCategory.category.in_(f.category)))
        )
    if f.area:
        stmt = stmt.where(Event.area.in_(f.area))
    if f.location:
        stmt = stmt.where(Event.location_id.in_(f.location))
    if f.source:
        stmt = stmt.where(Event.source_id.in_(f.source))
    if f.org:
        stmt = stmt.where(or_(*(org_clause(default_orgs()[o]) for o in f.org)))
    if f.q:
        pattern = f"%{f.q}%"
        stmt = stmt.where(
            or_(Event.title.ilike(pattern), Event.description.ilike(pattern), Event.location_name.ilike(pattern))
        )
    if f.free_food is not None:
        stmt = stmt.where(Event.free_food.is_(f.free_food))
    if not f.include_ongoing:
        stmt = stmt.where(Event.ongoing.is_(False))
    if not f.include_cancelled:
        stmt = stmt.where(Event.cancelled.is_(False))
    return stmt.order_by(Event.start, Event.id)


def org_clause(org: Org):
    """Events from the org's sources, held in its buildings, or naming one of its groups."""
    clauses = []
    if org.sources:
        clauses.append(Event.source_id.in_(org.sources))
    if org.locations:
        clauses.append(Event.location_id.in_(org.locations))
    if org.groups:
        e = aliased(Event)
        g = func.json_each(e.groups).table_valued("value")
        clauses.append(Event.id.in_(select(e.id).join(g, true()).where(g.c.value.in_(org.groups))))
    return or_(*clauses) if clauses else false()


# Keyset pagination: the cursor is the (start, id) of the last event on the previous page.

def encode_cursor(e: Event) -> str:
    return base64.urlsafe_b64encode(f"{e.start.isoformat()}|{e.id}".encode()).decode().rstrip("=")


def apply_cursor(stmt: Select[tuple[Event]], cursor: str) -> Select[tuple[Event]]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        start_s, id_ = raw.split("|", 1)
        start = datetime.fromisoformat(start_s)
    except ValueError:
        raise HTTPException(422, "invalid cursor") from None
    return stmt.where(or_(Event.start > start, and_(Event.start == start, Event.id > id_)))
