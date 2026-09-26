import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from yale_events.adapters import get_adapter
from yale_events.adapters.base import PoliteClient
from yale_events.config import SourceConfig
from yale_events.models import Event, ScrapeRun, Source, event_id, utcnow
from yale_events.schemas import FetchResult

log = logging.getLogger(__name__)

# Fields copied verbatim from RawEvent onto Event.
_COPIED = [
    "title", "description", "start", "end", "all_day", "location_name", "room", "address", "lat", "lon",
    "virtual", "url", "image_url", "tags", "keywords", "audience", "groups", "free", "cost", "cancelled",
    "source_updated_at",
]  # fmt: skip


def sync_source_row(session: Session, cfg: SourceConfig) -> None:
    session.merge(Source(id=cfg.id, name=cfg.name, type=cfg.type, url=cfg.url, enabled=cfg.enabled))


def upsert(session: Session, source_id: str, result: FetchResult, run: ScrapeRun) -> None:
    now = run.started_at
    existing = {
        e.source_event_id: e for e in session.scalars(select(Event).where(Event.source_id == source_id))
    }
    seen: set[str] = set()
    for raw in result.events:
        if raw.source_event_id in seen:
            continue
        seen.add(raw.source_event_id)
        values = {f: getattr(raw, f) for f in _COPIED}
        ev = existing.get(raw.source_event_id)
        if ev is None:
            ev = Event(
                id=event_id(source_id, raw.source_event_id),
                source_id=source_id,
                source_event_id=raw.source_event_id,
                first_seen=now,
                **values,
            )
            session.add(ev)
            run.inserted += 1
        else:
            changed = ev.stale or any(getattr(ev, f) != v for f, v in values.items())
            for f, v in values.items():
                setattr(ev, f, v)
            ev.stale = False
            if changed:
                ev.updated_at = now
                run.updated += 1
        ev.last_seen = now
    run.fetched = len(seen)

    # Anything in the fetched window that the source no longer lists has been removed upstream.
    res = session.execute(
        update(Event)
        .where(
            Event.source_id == source_id,
            Event.start >= result.window_start,
            Event.start < result.window_end,
            Event.last_seen < now,
            Event.stale.is_(False),
        )
        .values(stale=True, updated_at=now)
    )
    run.marked_stale = res.rowcount


def run_source(session: Session, cfg: SourceConfig, http: PoliteClient) -> ScrapeRun:
    sync_source_row(session, cfg)
    run = ScrapeRun(source_id=cfg.id, started_at=utcnow(), fetched=0, inserted=0, updated=0, marked_stale=0)
    session.add(run)
    session.commit()
    try:
        result = get_adapter(cfg.type, http).fetch(cfg)
        upsert(session, cfg.id, result, run)
        run.status = "ok"
    except Exception as exc:
        session.rollback()
        log.exception("scrape failed for %s", cfg.id)
        run = session.merge(run)
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"
    run.finished_at = utcnow()
    session.commit()
    return run
