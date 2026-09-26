import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from sqlalchemy import func, select

from yale_events.adapters.base import PoliteClient, make_client
from yale_events.config import DEFAULT_SOURCES_PATH, load_sources
from yale_events.db import make_session_factory
from yale_events.models import Event, ScrapeRun
from yale_events.pipeline import run_source

app = typer.Typer(no_args_is_help=True)
NEW_HAVEN = ZoneInfo("America/New_York")


@app.command()
def scrape(
    source: str | None = typer.Option(None, help="Only scrape this source id."),
    sources_file: Path = typer.Option(DEFAULT_SOURCES_PATH),
    cache_dir: Path = typer.Option(Path("data/cache"), help="Where raw responses are saved."),
):
    """Fetch events from enabled sources and upsert them into the database."""
    logging.basicConfig(level=logging.INFO)
    configs = [s for s in load_sources(sources_file) if s.enabled and (source is None or s.id == source)]
    if not configs:
        raise typer.BadParameter(f"no enabled source matching {source!r}")
    Session = make_session_factory()
    with make_client() as client, Session() as session:
        http = PoliteClient(client, min_interval=1.0, cache_dir=cache_dir)
        failed = False
        for cfg in configs:
            run = run_source(session, cfg, http)
            failed |= run.status != "ok"
            typer.echo(
                f"{cfg.id}: {run.status} fetched={run.fetched} new={run.inserted} "
                f"updated={run.updated} stale={run.marked_stale}" + (f" error={run.error}" if run.error else "")
            )
    raise typer.Exit(1 if failed else 0)


@app.command()
def sources(sources_file: Path = typer.Option(DEFAULT_SOURCES_PATH)):
    """List configured sources with their last scrape and upcoming event count."""
    Session = make_session_factory()
    now = datetime.now(UTC)
    with Session() as session:
        for cfg in load_sources(sources_file):
            last = session.scalars(
                select(ScrapeRun).where(ScrapeRun.source_id == cfg.id).order_by(ScrapeRun.id.desc()).limit(1)
            ).first()
            upcoming = session.scalar(
                select(func.count()).where(Event.source_id == cfg.id, Event.start >= now, Event.stale.is_(False))
            )
            status = f"{last.status} at {last.started_at:%Y-%m-%d %H:%M}Z" if last else "never run"
            flag = "" if cfg.enabled else " (disabled)"
            typer.echo(f"{cfg.id}{flag} [{cfg.type}] {status}, {upcoming} upcoming events")


@app.command()
def events(days: int = 3, limit: int = 20):
    """Print upcoming events (a quick check until the API exists)."""
    Session = make_session_factory()
    now = datetime.now(UTC)
    with Session() as session:
        rows = session.scalars(
            select(Event)
            .where(Event.start >= now - timedelta(hours=12), Event.start < now + timedelta(days=days))
            .where(Event.stale.is_(False), Event.cancelled.is_(False))
            .order_by(Event.start)
            .limit(limit)
        )
        for e in rows:
            when = "all day" if e.all_day else e.start.astimezone(NEW_HAVEN).strftime("%H:%M")
            day = e.start.astimezone(NEW_HAVEN).strftime("%a %m/%d")
            typer.echo(f"{day} {when:>7}  {e.title[:60]:<60}  {e.location_name or ''}")


if __name__ == "__main__":
    app()
