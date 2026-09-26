import plistlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from yale_events.adapters.base import prune_cache
from yale_events.config import SourceConfig
from yale_events.db import make_session_factory
from yale_events.health import MAX_AGE, check_runs, check_sources
from yale_events.models import ScrapeRun, Source
from yale_events.schedule import build_plist

NOW = datetime(2026, 9, 26, 20, tzinfo=UTC)


def run(hours_ago: float, status="ok", fetched=100, stale=0, error=None) -> ScrapeRun:
    return ScrapeRun(
        source_id="s", started_at=NOW - timedelta(hours=hours_ago), status=status,
        fetched=fetched, marked_stale=stale, error=error,
    )  # fmt: skip


def history(*runs):
    """Runs given oldest first, returned newest first like the query."""
    return list(reversed(runs))


@pytest.mark.parametrize(
    "runs, expected",
    [
        ([], [("warning", "never scraped")]),
        (history(run(12), run(8), run(4)), []),
        # A source that is normally empty stays quiet.
        (history(run(8, fetched=0), run(4, fetched=0)), []),
        (history(run(8), run(4, fetched=0)), [("error", "returned 0 events (usually ~100)")]),
        (history(run(8), run(4, fetched=40)), [("warning", "returned 40 events, down from ~100")]),
        # Small sources fluctuate; don't flag 6 -> 2.
        (history(run(8, fetched=6), run(4, fetched=2)), []),
        (history(run(8), run(4, fetched=100, stale=60)), [("warning", "60 events disappeared from the source")]),
        (history(run(20)), [("error", "last successful scrape was 20h ago")]),
        (history(run(8), run(4, "error", error="HTTPStatusError: 404"), run(0.1, "error", error="HTTPStatusError: 404")),
         [("error", "last 2 run(s) failed: HTTPStatusError: 404")]),
        (history(run(1), run(0.1, "error", error="x")), [("error", "last 1 run(s) failed: x")]),
        (history(run(3, "running")), [("error", "never finished"), ("error", "no successful scrape")]),
    ],
)  # fmt: skip
def test_check_runs(runs, expected):
    problems = check_runs(runs, NOW, MAX_AGE)
    assert [level for level, _ in problems] == [level for level, _ in expected]
    for (_, msg), (_, want) in zip(problems, expected):
        assert want in msg


def test_check_sources_skips_disabled_and_reads_runs_from_db():
    with make_session_factory("sqlite://")() as session:
        for sid in ("a", "b", "off"):
            session.add(Source(id=sid, name=sid, type="ical", url="x"))
        session.add_all([
            ScrapeRun(source_id="a", started_at=NOW - timedelta(hours=8), status="ok", fetched=50),
            ScrapeRun(source_id="a", started_at=NOW - timedelta(hours=4), status="ok", fetched=0),
            ScrapeRun(source_id="b", started_at=NOW - timedelta(hours=4), status="ok", fetched=5),
        ])  # fmt: skip
        session.commit()
        configs = [
            SourceConfig(id=sid, name=sid, type="ical", url="x", enabled=sid != "off") for sid in ("a", "b", "off")
        ]
        problems = check_sources(session, configs, NOW)
    assert [(p.source_id, p.level) for p in problems] == [("a", "error")]


def test_prune_cache_keeps_latest_runs_per_source(tmp_path):
    for source in ("yale-central", "td"):
        for stamp in ("20260101T000000", "20260102T000000", "20260103T000000"):
            for page in (1, 2):
                (tmp_path / f"{source}-{stamp}-p{page}.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("unrelated files are left alone")
    assert prune_cache(tmp_path, keep_runs=2) == 4
    names = sorted(p.name for p in tmp_path.iterdir())
    assert "notes.txt" in names
    assert not any("20260101" in n for n in names)
    assert sum("yale-central-20260103" in n for n in names) == 2


def test_launchd_plist(tmp_path, monkeypatch):
    monkeypatch.setenv("YEV_CONTACT", "someone@example.edu")
    plist = build_plist(tmp_path, 4, python="/venv/bin/python")
    assert plist["StartInterval"] == 4 * 3600
    assert plist["WorkingDirectory"] == str(tmp_path.resolve())
    assert plist["EnvironmentVariables"]["PYTHONPATH"] == str(tmp_path.resolve() / "src")
    assert plist["EnvironmentVariables"]["YEV_CONTACT"] == "someone@example.edu"
    command = plist["ProgramArguments"][-1]
    assert "/venv/bin/python -m yale_events.cli scrape;" in command
    assert command.endswith("sources check --notify")
    assert Path(plist["StandardOutPath"]).parent == tmp_path.resolve() / "data/logs"
    plistlib.dumps(plist)  # serializable
