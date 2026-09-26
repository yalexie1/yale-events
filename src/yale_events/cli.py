import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from sqlalchemy import func, select

from yale_events.adapters.base import PoliteClient, ReplayClient, make_client
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
    replay: bool = typer.Option(False, help="Re-process the latest cached responses instead of fetching."),
):
    """Fetch events from enabled sources and upsert them into the database."""
    logging.basicConfig(level=logging.INFO)
    configs = [s for s in load_sources(sources_file) if s.enabled and (source is None or s.id == source)]
    if not configs:
        raise typer.BadParameter(f"no enabled source matching {source!r}")
    Session = make_session_factory()
    with make_client() as client, Session() as session:
        http = ReplayClient(cache_dir) if replay else PoliteClient(client, min_interval=1.0, cache_dir=cache_dir)
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
def events(days: int = 3, limit: int = 20, ongoing: bool = typer.Option(False, help="Include exhibitions.")):
    """Print upcoming events (a quick look without starting the API)."""
    Session = make_session_factory()
    now = datetime.now(UTC)
    with Session() as session:
        rows = session.scalars(
            select(Event)
            .where(Event.start >= now - timedelta(hours=12), Event.start < now + timedelta(days=days))
            .where(Event.stale.is_(False), Event.cancelled.is_(False))
            .where(True if ongoing else Event.ongoing.is_(False))
            .order_by(Event.start)
            .limit(limit)
        )
        for e in rows:
            when = "all day" if e.all_day else e.start.astimezone(NEW_HAVEN).strftime("%H:%M")
            day = e.start.astimezone(NEW_HAVEN).strftime("%a %m/%d")
            where = e.location_id or e.location_name or ""
            typer.echo(f"{day} {when:>7}  {e.title[:50]:<50}  {','.join(e.categories):<22} {e.area or '?':<13} {where}")


@app.command()
def uncategorized(limit: int = 40):
    """Upcoming titles with no category or no location match, most frequent first, to guide new rules."""
    Session = make_session_factory()
    with Session() as session:
        rows = list(session.scalars(select(Event).where(Event.stale.is_(False))))
        no_cat = Counter(e.title for e in rows if not e.categories)
        no_loc = Counter(e.location_name for e in rows if e.area is None)
        typer.echo(f"{sum(no_cat.values())}/{len(rows)} occurrences without a category:")
        for title, n in no_cat.most_common(limit):
            typer.echo(f"  {n:4}  {title[:90]}")
        typer.echo(f"\n{sum(no_loc.values())}/{len(rows)} occurrences without an area:")
        for name, n in no_loc.most_common(limit):
            typer.echo(f"  {n:4}  {name}")


@app.command()
def discover(urls: list[str]):
    """Look for event feeds (iCal, RSS, Google Calendar, Localist) on candidate source sites."""
    from yale_events.discover import Discoverer

    with make_client() as client:
        d = Discoverer(client)
        for url in urls:
            r = d.discover(url)
            typer.echo(f"\n{url}" + (f" -> {r.final_url}" if r.final_url and r.final_url.rstrip("/") != url.rstrip("/") else ""))
            for p in r.pages[1:]:
                typer.echo(f"  followed {p}")
            for b in r.blocked_by_robots:
                typer.echo(f"  robots.txt disallows {b}")
            if r.error:
                typer.echo(f"  error: {r.error}")
            for f in r.feeds:
                typer.echo(f"  {f.kind:<16} {f.url}")
            if not r.feeds and not r.error:
                typer.echo(f"  no feeds found (status {r.status})")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """Run the API (docs at /docs)."""
    import uvicorn

    uvicorn.run("yale_events.api.main:create_app", factory=True, host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
