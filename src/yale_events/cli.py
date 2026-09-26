import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import typer
from sqlalchemy import func, select

from yale_events.adapters.base import PoliteClient, ReplayClient, make_client, prune_cache
from yale_events.config import DEFAULT_SOURCES_PATH, load_sources
from yale_events.db import make_session_factory
from yale_events.dedupe import dedupe
from yale_events.models import Event, ScrapeRun
from yale_events.pipeline import run_source

app = typer.Typer(no_args_is_help=True)
sources_app = typer.Typer(help="Configured sources and their scrape health.")
schedule_app = typer.Typer(help="Scrape automatically every few hours (macOS launchd).", no_args_is_help=True)
app.add_typer(sources_app, name="sources")
app.add_typer(schedule_app, name="schedule")
NEW_HAVEN = ZoneInfo("America/New_York")


@app.command()
def scrape(
    source: str | None = typer.Option(None, help="Only scrape this source id."),
    sources_file: Path = typer.Option(DEFAULT_SOURCES_PATH),
    cache_dir: Path = typer.Option(Path("data/cache"), help="Where raw responses are saved."),
    replay: bool = typer.Option(False, help="Re-process the latest cached responses instead of fetching."),
    keep_cache_runs: int = typer.Option(3, help="Cached runs to keep per source; older ones are deleted."),
):
    """Fetch events from enabled sources and upsert them into the database."""
    logging.basicConfig(level=logging.INFO)
    all_sources = load_sources(sources_file)
    configs = [s for s in all_sources if s.enabled and (source is None or s.id == source)]
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
        dupes = dedupe(session, [s.id for s in all_sources], since=datetime.now(UTC) - timedelta(days=1))
        typer.echo(f"dedupe: {dupes} events hidden as duplicates of another source's listing")
    if not replay and cache_dir.exists():
        prune_cache(cache_dir, keep_cache_runs)
    raise typer.Exit(1 if failed else 0)


@sources_app.callback(invoke_without_command=True)
def sources(ctx: typer.Context, sources_file: Path = typer.Option(DEFAULT_SOURCES_PATH)):
    """List configured sources with their last scrape and upcoming event count."""
    if ctx.invoked_subcommand:
        return
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


@sources_app.command("check")
def sources_check(
    sources_file: Path = typer.Option(DEFAULT_SOURCES_PATH),
    max_age_hours: float = typer.Option(12, help="Flag sources with no successful scrape in this long."),
    notify: bool = typer.Option(False, help="Post a macOS notification if anything needs attention."),
):
    """Flag enabled sources that are failing, overdue, or suddenly returning far fewer events.

    Exits 1 if any source has an error-level problem."""
    from yale_events.health import check_sources

    configs = load_sources(sources_file)
    with make_session_factory()() as session:
        problems = check_sources(session, configs, datetime.now(UTC), timedelta(hours=max_age_hours))
    flagged = {p.source_id for p in problems}
    for cfg in configs:
        if cfg.enabled and cfg.id not in flagged:
            typer.echo(f"ok       {cfg.id}")
    for p in problems:
        typer.echo(f"{p.level:<8} {p.source_id}: {p.message}")
    errors = [p for p in problems if p.level == "error"]
    if notify and problems:
        from yale_events.schedule import notify as post

        worst = errors[0] if errors else problems[0]
        more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
        post("Yale Events: source check", f"{worst.source_id}: {worst.message}{more}")
    raise typer.Exit(1 if errors else 0)


@schedule_app.command("install")
def schedule_install(every: float = typer.Option(4, help="Hours between scrapes.")):
    """Install a launchd agent that scrapes now and then every N hours, then runs `sources check --notify`."""
    from yale_events import schedule

    path = schedule.install(Path.cwd(), every)
    typer.echo(f"installed {path}; scraping every {every:g}h, log in data/logs/scrape.log")


@schedule_app.command("uninstall")
def schedule_uninstall():
    """Stop scheduled scrapes."""
    from yale_events import schedule

    typer.echo("removed" if schedule.uninstall() else "not installed")


@schedule_app.command("status")
def schedule_status():
    """Whether the scheduled job is loaded, when it last exited, and the tail of its log."""
    from yale_events import schedule

    info = schedule.status()
    if info is None:
        typer.echo("not installed")
        raise typer.Exit(1)
    for line in info.splitlines():
        if any(k in line for k in ("state =", "run interval", "last exit code", "runs =")):
            typer.echo(line.strip())
    log = Path("data/logs/scrape.log")
    if log.exists():
        typer.echo("\n".join(["", f"--- {log} (last 15 lines)", *log.read_text().splitlines()[-15:]]))


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
