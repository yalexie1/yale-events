from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from yale_events.api.ical import to_ics
from yale_events.api.query import EventFilters, apply_cursor, build_query, encode_cursor, parse_when, split_values, validate
from yale_events.api.schemas import AreaOut, BuildingOut, CategoryOut, EventOut, EventPage, SourceOut
from yale_events.db import make_session_factory
from yale_events.models import Event, EventCategory, ScrapeRun, Source
from yale_events.normalize import default_normalizer
from yale_events.normalize.time import NEW_HAVEN

DEFAULT_DAYS = 14
ICS_PAST_DAYS = 7
MAX_LIMIT = 500
ICS_MAX_EVENTS = 5000
ICS_MEDIA_TYPE = "text/calendar; charset=utf-8"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    app = FastAPI(
        title="Yale Events",
        version="0.1.0",
        description=(
            "A unified feed of Yale events. Every filter on `/events` also works on `/events.ics`, "
            "so any filtered view can be subscribed to as a calendar."
        ),
    )
    app.state.session_factory = session_factory or make_session_factory()
    register_routes(app)
    return app


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def event_filters(
    start: Annotated[
        str | None, Query(description="YYYY-MM-DD or ISO datetime (New Haven time if no offset). Default: now.")
    ] = None,
    end: Annotated[str | None, Query(description="YYYY-MM-DD (inclusive) or ISO datetime.")] = None,
    category: Annotated[list[str] | None, Query(description="Any of these; repeat or comma-separate.")] = None,
    area: Annotated[list[str] | None, Query(description="Any of these areas, see /areas.")] = None,
    location: Annotated[list[str] | None, Query(description="Any of these buildings, see /locations.")] = None,
    source: Annotated[list[str] | None, Query(description="Any of these sources, see /sources.")] = None,
    q: Annotated[str | None, Query(description="Text search in title, description, and venue.")] = None,
    free_food: bool | None = None,
    include_ongoing: Annotated[
        bool, Query(description="Include daily occurrences of long-running exhibitions.")
    ] = False,
    include_cancelled: bool = False,
) -> EventFilters:
    f = EventFilters(
        start=parse_when(start, "start"),
        end=parse_when(end, "end", end=True),
        category=split_values(category),
        area=split_values(area),
        location=split_values(location),
        source=split_values(source),
        q=q.strip() or None if q else None,
        free_food=free_food,
        include_ongoing=include_ongoing,
        include_cancelled=include_cancelled,
    )
    validate(f)
    return f


FiltersDep = Annotated[EventFilters, Depends(event_filters)]


def upcoming_counts(session: Session, column, now: datetime) -> dict[str, int]:
    """Count of events per value of `column` from now on, as the default feed would show them."""
    stmt = (
        select(column, func.count())
        .select_from(Event)
        .where(Event.start >= now, Event.stale.is_(False), Event.cancelled.is_(False), Event.ongoing.is_(False))
        .where(Event.duplicate_of.is_(None))
        .group_by(column)
    )
    if column is EventCategory.category:
        stmt = stmt.join(EventCategory, EventCategory.event_id == Event.id)
    return dict(session.execute(stmt).all())


def calendar_name(f: EventFilters) -> str:
    n = default_normalizer()
    parts = [n.categorizer.categories[c] for c in f.category]
    parts += [n.locations.areas[a] for a in f.area]
    parts += [n.locations.buildings[b].name for b in f.location]
    if f.free_food:
        parts.append("Free food")
    if f.q:
        parts.append(f"“{f.q}”")
    return "Yale Events" + (f": {', '.join(parts)}" if parts else "")


def register_routes(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    def home():
        """A small browser UI over the API."""
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/events", response_model=EventPage, tags=["events"])
    def list_events(
        session: SessionDep,
        f: FiltersDep,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
        cursor: str | None = None,
    ):
        """Events overlapping [start, end), soonest first. Defaults to the next 14 days, without
        cancelled events or daily occurrences of ongoing exhibitions."""
        f.start = f.start or datetime.now(UTC)
        f.end = f.end or f.start + timedelta(days=DEFAULT_DAYS)
        validate(f)
        stmt = build_query(f)
        if cursor:
            stmt = apply_cursor(stmt, cursor)
        rows = list(session.scalars(stmt.limit(limit + 1)))
        dupes = duplicates_of(session, [e.id for e in rows[:limit]])
        locations = default_normalizer().locations
        return EventPage(
            events=[EventOut.from_event(e, locations, dupes.get(e.id, [])) for e in rows[:limit]],
            next_cursor=encode_cursor(rows[limit - 1]) if len(rows) > limit else None,
        )

    # Registered before /events/{event_id}, which would otherwise capture "abc.ics".
    @app.get("/events.ics", tags=["ical"], response_class=Response)
    def events_ics(session: SessionDep, f: FiltersDep):
        """Subscribable calendar with the same filters as /events. Defaults to one week back through
        everything scraped ahead, and includes cancelled events (marked CANCELLED) so subscribed
        calendars update them."""
        if f.start is None:
            today = datetime.now(NEW_HAVEN).date()
            f.start = datetime.combine(today - timedelta(days=ICS_PAST_DAYS), time(), NEW_HAVEN)
        f.include_cancelled = True
        validate(f)
        events = session.scalars(build_query(f).limit(ICS_MAX_EVENTS))
        return Response(to_ics(events, calendar_name(f)), media_type=ICS_MEDIA_TYPE)

    @app.get("/events/{event_id}.ics", tags=["ical"], response_class=Response)
    def event_ics(session: SessionDep, event_id: str):
        e = get_event_or_404(session, event_id)
        return Response(
            to_ics([e], e.title),
            media_type=ICS_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{event_id}.ics"'},
        )

    @app.get("/events/{event_id}", response_model=EventOut, tags=["events"])
    def get_event(session: SessionDep, event_id: str):
        e = get_event_or_404(session, event_id)
        if e.duplicate_of:  # a merged listing: show the canonical event
            e = get_event_or_404(session, e.duplicate_of)
        dupes = duplicates_of(session, [e.id]).get(e.id, [])
        return EventOut.from_event(e, default_normalizer().locations, dupes)

    @app.get("/categories", response_model=list[CategoryOut], tags=["reference"])
    def categories(session: SessionDep):
        counts = upcoming_counts(session, EventCategory.category, datetime.now(UTC))
        return [
            CategoryOut(id=c, name=name, upcoming=counts.get(c, 0))
            for c, name in default_normalizer().categorizer.categories.items()
        ]

    @app.get("/areas", response_model=list[AreaOut], tags=["reference"])
    def areas(session: SessionDep):
        counts = upcoming_counts(session, Event.area, datetime.now(UTC))
        return [
            AreaOut(id=a, name=name, upcoming=counts.get(a, 0))
            for a, name in default_normalizer().locations.areas.items()
        ]

    @app.get("/locations", response_model=list[BuildingOut], tags=["reference"])
    def locations(session: SessionDep, area: str | None = None):
        counts = upcoming_counts(session, Event.location_id, datetime.now(UTC))
        return [
            BuildingOut(id=b.id, name=b.name, area=b.area, lat=b.lat, lon=b.lon, upcoming=counts.get(b.id, 0))
            for b in default_normalizer().locations.buildings.values()
            if area is None or b.area == area
        ]

    @app.get("/sources", response_model=list[SourceOut], tags=["reference"])
    def sources(session: SessionDep):
        counts = upcoming_counts(session, Event.source_id, datetime.now(UTC))
        out = []
        for s in session.scalars(select(Source).order_by(Source.id)):
            last = session.scalars(
                select(ScrapeRun).where(ScrapeRun.source_id == s.id).order_by(ScrapeRun.id.desc()).limit(1)
            ).first()
            out.append(
                SourceOut(
                    id=s.id,
                    name=s.name,
                    type=s.type,
                    url=s.url,
                    enabled=s.enabled,
                    last_run_at=last.started_at if last else None,
                    last_run_status=last.status if last else None,
                    last_run_error=last.error if last else None,
                    upcoming=counts.get(s.id, 0),
                )
            )
        return out


def duplicates_of(session: Session, ids: list[str]) -> dict[str, list[Event]]:
    out: dict[str, list[Event]] = {}
    if ids:
        for d in session.scalars(select(Event).where(Event.duplicate_of.in_(ids)).order_by(Event.source_id)):
            out.setdefault(d.duplicate_of, []).append(d)
    return out


def get_event_or_404(session: Session, event_id: str) -> Event:
    if (e := session.get(Event, event_id)) is None:
        raise HTTPException(404, "event not found")
    return e
