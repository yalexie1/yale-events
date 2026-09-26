"""Flags sources whose scrapes are failing, stuck, overdue, or suddenly returning far fewer events."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from yale_events.config import SourceConfig
from yale_events.models import ScrapeRun

MAX_AGE = timedelta(hours=12)  # three missed runs at the default 4h schedule
STUCK_AFTER = timedelta(hours=1)
HISTORY = 5  # previous successful runs used as the baseline event count
DROP_RATIO = 0.5
MIN_BASELINE = 10  # don't flag drops on sources that normally list only a handful


@dataclass
class Problem:
    source_id: str
    level: str  # "error" (exit non-zero) or "warning"
    message: str


def check_sources(
    session: Session, configs: list[SourceConfig], now: datetime, max_age: timedelta = MAX_AGE
) -> list[Problem]:
    problems = []
    for cfg in configs:
        if cfg.enabled:
            runs = list(
                session.scalars(
                    select(ScrapeRun).where(ScrapeRun.source_id == cfg.id).order_by(ScrapeRun.id.desc()).limit(50)
                )
            )
            problems += [Problem(cfg.id, level, msg) for level, msg in check_runs(runs, now, max_age)]
    return problems


def check_runs(runs: list[ScrapeRun], now: datetime, max_age: timedelta) -> list[tuple[str, str]]:
    """`runs` newest first."""
    if not runs:
        return [("warning", "never scraped")]
    out = []
    last = runs[0]
    if last.status == "running" and now - last.started_at > STUCK_AFTER:
        out.append(("error", f"run started {ago(now, last.started_at)} never finished (crashed or killed?)"))
    failures = 0
    for r in runs:
        if r.status != "error":
            break
        failures += 1
    if failures:
        out.append(("error", f"last {failures} run(s) failed: {last.error}"))

    ok = [r for r in runs if r.status == "ok"]
    if not ok:
        out.append(("error", "no successful scrape on record"))
        return out
    if now - ok[0].started_at > max_age:
        out.append(("error", f"last successful scrape was {ago(now, ok[0].started_at)}"))

    latest, previous = ok[0], ok[1 : 1 + HISTORY]
    if previous:
        baseline = median(r.fetched for r in previous)
        if latest.fetched == 0 and baseline > 0:
            out.append(("error", f"returned 0 events (usually ~{baseline:g}); the page or feed may have changed"))
        elif baseline >= MIN_BASELINE and latest.fetched < baseline * DROP_RATIO:
            out.append(("warning", f"returned {latest.fetched} events, down from ~{baseline:g}"))
    if latest.marked_stale >= MIN_BASELINE and latest.marked_stale > latest.fetched * DROP_RATIO:
        out.append(("warning", f"{latest.marked_stale} events disappeared from the source in the last run"))
    return out


def ago(now: datetime, then: datetime) -> str:
    hours = (now - then).total_seconds() / 3600
    return f"{hours:.0f}h ago" if hours < 48 else f"{hours / 24:.0f} days ago"
