import json
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from yale_events.adapters.base import CacheNamer, PoliteClient, ReplayClient
from yale_events.adapters.engineering import EngineeringAdapter, parse_item
from yale_events.adapters.ical import parse_calendar
from yale_events.adapters.text import clean_description, clean_field
from yale_events.adapters.yalesites import YaleSitesAdapter, event_links, parse_event_page
from yale_events.config import SourceConfig
from yale_events.normalize.time import NEW_HAVEN

FIXTURES = Path(__file__).parent / "fixtures"
START = datetime(2026, 10, 1, tzinfo=NEW_HAVEN)
END = START + timedelta(days=90)


# --- text ------------------------------------------------------------------------------------

def test_clean_field_strips_labels_tags_and_double_escaping():
    assert clean_field("Location \nSLB Room 127 ") == "SLB Room 127"
    assert clean_field("Law &amp;amp; Economics") == "Law & Economics"
    assert clean_field("<b>Bold</b>  title") == "Bold title"
    assert clean_description("Description \n\n<p>One.</p><p>Two&nbsp;words.</p>") == "One.\nTwo words."


# --- ical ------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ics() -> bytes:
    return (FIXTURES / "sample.ics").read_bytes()


def by_title(events):
    return {e.title: e for e in events}


def test_ical_basic_fields(ics):
    talk = by_title(parse_calendar(ics, START, END))["Law & Economics Workshop"]
    assert talk.source_event_id == "talk-1@test"
    assert talk.start == datetime(2026, 10, 5, 16, 10, tzinfo=NEW_HAVEN)
    assert talk.end - talk.start == timedelta(minutes=80)
    assert (talk.location_name, talk.description) == ("SLB Room 127", "Lunch will be served.")
    assert talk.tags == ["Lecture", "Law"]
    assert talk.url == "https://example.edu/talk-1"


def test_ical_recurrence_exdate_and_override(ics):
    knitting = sorted((e for e in parse_calendar(ics, START, END) if e.title.startswith("Knitting")), key=lambda e: e.start)
    # Oct 21, (Oct 28 excluded), Nov 4 moved to 8 PM and renamed.
    assert [(e.start.date(), e.start.hour, e.title) for e in knitting] == [
        (date(2026, 10, 21), 18, "Knitting Club"),
        (date(2026, 11, 4), 20, "Knitting Club (late)"),
    ]
    assert len({e.source_event_id for e in knitting}) == 2
    assert all(e.series_id == "weekly@test" for e in knitting)
    # The moved occurrence keeps the id of its original slot, so edits update rather than duplicate.
    assert knitting[1].source_event_id.startswith("weekly@test/2026-11-04T18:00")


def test_ical_all_day_private_and_window(ics):
    events = by_title(parse_calendar(ics, START, END))
    holiday = events["Indigenous Peoples’ Day; no classes will meet."]
    assert holiday.all_day and holiday.end is None
    assert "Busy" not in events  # CLASS:PRIVATE
    assert "Long ago" not in events  # outside the window


def test_ical_options(ics):
    options = {
        "location_contains": "New Haven",
        "title_strip": r"^(\[[A-Z]\]\s*)?Yale University\s+",
        "exclude_title": "no classes will meet",
    }
    events = parse_calendar(ics, START, END, options)
    assert [(e.title, e.cancelled) for e in events] == [("Football vs Dartmouth", True)]


# --- engineering -----------------------------------------------------------------------------

def test_engineering_item_with_detail_page():
    item = json.loads((FIXTURES / "engineering_list.json").read_text())["events"][0]
    ev = parse_item(item, (FIXTURES / "engineering_event.html").read_text())
    assert ev.title == "ASML Tech Talk & Recruitment Event"
    assert ev.start == datetime(2026, 9, 28, 16, tzinfo=NEW_HAVEN)
    assert ev.end == datetime(2026, 9, 28, 17, tzinfo=NEW_HAVEN)
    assert ev.location_name == "Center for Engineering Innovation & Design (CEID), 15 Prospect Street"
    assert ev.description.endswith("Pizza will be provided!")
    assert ev.groups == []  # "All Departments" is dropped


def test_engineering_item_without_times_is_all_day():
    ev = parse_item({"id": 1, "title": "Open House", "date": "October 2, 2026", "startTime": "", "endTime": ""})
    assert ev.all_day and ev.end is None and ev.start == datetime(2026, 10, 2, tzinfo=NEW_HAVEN)


@respx.mock
def test_engineering_fetch_pages_and_details(tmp_path):
    url = "https://engineering.yale.edu/news-and-events/events/get_events/1421"
    page = json.loads((FIXTURES / "engineering_list.json").read_text())
    list_route = respx.post(url).mock(
        side_effect=[
            httpx.Response(200, json={**page, "showMore": True}),
            httpx.Response(200, json={"events": [], "showMore": False}),
        ]
    )
    respx.get(url__startswith="https://engineering.yale.edu/news-and-events/events/").mock(
        return_value=httpx.Response(200, text=(FIXTURES / "engineering_event.html").read_text())
    )
    http = PoliteClient(httpx.Client(), min_interval=0, cache_dir=tmp_path)
    result = EngineeringAdapter(http).fetch(SourceConfig(id="engineering", name="E", type="engineering", url=url))
    assert list_route.call_count == 2
    assert b"currentPage=2" in list_route.calls[1].request.content
    assert len(result.events) == 2
    # Two list pages + two detail pages cached, in request order, for replay.
    assert sorted(p.name.rpartition("-")[2] for p in tmp_path.iterdir()) == ["p1.json", "p2.json", "p3.html", "p4.html"]


# --- yalesites -------------------------------------------------------------------------------

TD_URL = "https://timothydwight.yale.edu/events/2026-09-16-td-knit-and-crochet-club"


def test_yalesites_recurring_page_lists_every_upcoming_date():
    events = parse_event_page((FIXTURES / "yalesites_recurring.html").read_text(), TD_URL)
    assert len(events) == 12
    first = events[0]
    assert first.title == "TD Knit and Crochet Club"
    assert first.start == datetime(2026, 9, 30, 18, tzinfo=NEW_HAVEN)
    assert first.end - first.start == timedelta(hours=1)  # from the embedded iCal
    assert len({e.source_event_id for e in events}) == 12
    assert {e.series_id for e in events} == {"/events/2026-09-16-td-knit-and-crochet-club"}


def test_yalesites_all_day_page():
    url = "https://chaplain.yale.edu/events/2026-10-03-shemini-atzeret-judaism"
    (ev,) = parse_event_page((FIXTURES / "yalesites_all_day.html").read_text(), url)
    assert ev.all_day and ev.end is None
    assert ev.start.date() == date(2026, 10, 3)
    assert ev.description.startswith("Marks the end of Sukkot")
    assert ev.series_id is None


def test_yalesites_links_same_host_only_and_deduplicated():
    html = """<a href="/events/2026-10-01-a">A</a><a href="/events/2026-10-01-a#x">A again</a>
    <a href="https://other.yale.edu/events/2026-10-02-b">B</a><a href="/events">all</a>"""
    assert event_links(html, "https://td.yale.edu/") == ["https://td.yale.edu/events/2026-10-01-a"]


# --- replay ----------------------------------------------------------------------------------

def test_replay_serves_latest_run_for_any_extension(tmp_path):
    for stamp, body in [("20260101T000000", b"old"), ("20260201T000000", b"new")]:
        (tmp_path / f"td-{stamp}-p1.html").write_bytes(body + b"-listing")
        (tmp_path / f"td-{stamp}-p2.html").write_bytes(body + b"-event")
    replay = ReplayClient(tmp_path)
    names = CacheNamer("td")
    assert replay.get_text("https://x", cache_name=names.next("html")) == "new-listing"
    assert replay.get_text("https://y", cache_name=names.next("html")) == "new-event"


@respx.mock
def test_yalesites_fetch_filters_to_window(tmp_path):
    respx.get("https://timothydwight.yale.edu/").mock(
        return_value=httpx.Response(200, text='<a href="/events/2026-09-16-td-knit-and-crochet-club">x</a>')
    )
    respx.get(TD_URL).mock(return_value=httpx.Response(200, text=(FIXTURES / "yalesites_recurring.html").read_text()))
    http = PoliteClient(httpx.Client(), min_interval=0)
    src = SourceConfig(id="td", name="TD", type="yalesites", url="https://timothydwight.yale.edu/", options={"days": 3650})
    result = YaleSitesAdapter(http).fetch(src)
    assert all(e.start >= result.window_start for e in result.events)
    assert 0 < len(result.events) <= 12
