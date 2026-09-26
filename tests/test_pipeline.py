from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from yale_events.config import SourceConfig
from yale_events.db import make_session_factory
from yale_events.models import Event, ScrapeRun
from yale_events.pipeline import run_source
from yale_events.schemas import FetchResult, RawEvent

NOW = datetime.now(UTC)
CFG = SourceConfig(id="test", name="Test", type="fake", url="https://example.edu")


def raw(i: int, **kw) -> RawEvent:
    return RawEvent(**{"source_event_id": str(i), "title": f"Event {i}", "start": NOW + timedelta(days=i), **kw})


class FakeAdapter:
    def __init__(self, events):
        self.events = events

    def fetch(self, source):
        if isinstance(self.events, Exception):
            raise self.events
        return FetchResult(events=self.events, window_start=NOW, window_end=NOW + timedelta(days=30))


@pytest.fixture
def session(monkeypatch):
    adapter = FakeAdapter([])
    monkeypatch.setattr("yale_events.pipeline.get_adapter", lambda type_, http: adapter)
    with make_session_factory("sqlite://")() as s:
        s.adapter = adapter
        yield s


def scrape(session, events):
    session.adapter.events = events
    return run_source(session, CFG, http=None)


def test_insert_update_and_stale(session):
    run = scrape(session, [raw(1), raw(2), raw(3)])
    assert (run.status, run.inserted, run.updated, run.marked_stale) == ("ok", 3, 0, 0)

    # Unchanged re-scrape: nothing updated.
    run = scrape(session, [raw(1), raw(2), raw(3)])
    assert (run.inserted, run.updated, run.marked_stale) == (0, 0, 0)

    # Event 2 renamed, event 3 removed upstream.
    run = scrape(session, [raw(1), raw(2, title="Renamed")])
    assert (run.inserted, run.updated, run.marked_stale) == (0, 1, 1)
    events = {e.source_event_id: e for e in session.scalars(select(Event))}
    assert events["2"].title == "Renamed"
    assert events["3"].stale is True

    # Event 3 reappears: un-staled and counted as updated.
    run = scrape(session, [raw(1), raw(2, title="Renamed"), raw(3)])
    assert run.updated == 1
    assert session.get(Event, events["3"].id).stale is False


def test_start_times_round_trip_as_utc(session):
    scrape(session, [raw(1)])
    ev = session.scalars(select(Event)).one()
    assert ev.start.tzinfo is not None
    assert abs(ev.start - (NOW + timedelta(days=1))) < timedelta(seconds=1)


def test_adapter_error_recorded(session):
    run = scrape(session, RuntimeError("boom"))
    assert run.status == "error"
    assert "boom" in run.error
    assert session.scalars(select(ScrapeRun)).one().status == "error"
