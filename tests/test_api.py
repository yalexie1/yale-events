from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yale_events.api.main import create_app
from yale_events.models import Base, Event, ScrapeRun, Source
from yale_events.normalize.time import NEW_HAVEN

NOW = datetime.now(UTC)
TODAY_MIDNIGHT = datetime.combine(datetime.now(NEW_HAVEN).date(), time(), NEW_HAVEN)


def ev(key: str, start: datetime, categories=(), **kw) -> Event:
    e = Event(
        id=key, source_id="test", source_event_id=key, title=kw.pop("title", key.replace("-", " ").title()),
        start=start, **kw,
    )  # fmt: skip
    e.categories.extend(categories)
    return e


def make_events() -> list[Event]:
    return [
        ev("jazz", NOW + timedelta(days=1), ["music"], area="central", location_id="woolsey-hall", free_food=True,
           description="Pizza after the show.", url="https://events.yale.edu/jazz",
           groups=["Yale School of Music", "Yale Arts"]),
        ev("lecture", NOW + timedelta(days=2), ["talks"], area="science-hill", location_id="kroon-hall",
           end=NOW + timedelta(days=2, hours=1), location_name="Kroon Hall", room="Burke Auditorium", lat=41.3167,
           lon=-72.9235, groups=["School of the Environment"]),
        ev("in-progress", NOW - timedelta(hours=1), ["talks"], end=NOW + timedelta(hours=1), area="central"),
        ev("today-all-day", TODAY_MIDNIGHT, ["social"], all_day=True, area="central"),
        ev("past", NOW - timedelta(days=1), ["talks"], end=NOW - timedelta(days=1) + timedelta(hours=1)),
        ev("far", NOW + timedelta(days=120), ["film"]),
        ev("exhibit", NOW + timedelta(days=1), ["exhibitions"], all_day=True, ongoing=True, area="arts-district"),
        ev("cancelled", NOW + timedelta(days=3), ["talks"], cancelled=True),
        ev("stale", NOW + timedelta(days=3), ["talks"], stale=True),
        ev("online", NOW + timedelta(days=4), ["career"], area="online", virtual=True, title="Resume Workshop"),
        # Another source's listing of "jazz", merged by dedupe.
        ev("jazz-copy", NOW + timedelta(days=1), ["music"], duplicate_of="jazz", url="https://other.edu/jazz"),
    ]  # fmt: skip


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        s.add(Source(id="test", name="Test", type="fake", url="https://example.edu"))
        s.add(ScrapeRun(source_id="test", status="ok", fetched=10))
        s.add_all(make_events())
        s.commit()
    return TestClient(create_app(factory))


def ids(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [e["id"] for e in resp.json()["events"]]


def test_default_window(client):
    # Next 90 days, including events in progress and today's all-day ones; no past, far, ongoing,
    # cancelled, or stale events.
    assert ids(client.get("/events")) == ["today-all-day", "in-progress", "jazz", "lecture", "online"]


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"category": "talks"}, ["in-progress", "lecture"]),
        ({"category": ["talks", "music"]}, ["in-progress", "jazz", "lecture"]),
        ({"category": "talks,music"}, ["in-progress", "jazz", "lecture"]),
        ({"area": "science-hill"}, ["lecture"]),
        ({"location": "woolsey-hall"}, ["jazz"]),
        ({"q": "pizza"}, ["jazz"]),
        ({"q": "RESUME"}, ["online"]),
        ({"free_food": "true"}, ["jazz"]),
        ({"include_ongoing": "true", "area": "arts-district"}, ["exhibit"]),
        ({"include_cancelled": "true", "category": "talks"}, ["in-progress", "lecture", "cancelled"]),
        ({"source": "other"}, []),
        ({"org": "music"}, ["jazz"]),  # by group
        ({"org": "music,environment"}, ["jazz", "lecture"]),
        ({"org": "berkeley"}, []),  # by building only
    ],
)
def test_filters(client, params, expected):
    assert ids(client.get("/events", params=params)) == expected


def test_date_range_end_is_inclusive(client):
    day = (NOW + timedelta(days=120)).astimezone(NEW_HAVEN).date()
    assert ids(client.get("/events", params={"start": day.isoformat(), "end": day.isoformat()})) == ["far"]


def test_start_datetime_with_unencoded_plus(client):
    start = (NOW + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    assert ids(client.get(f"/events?start={start}")) == ["online"]


@pytest.mark.parametrize(
    "params, message",
    [
        ({"category": "nope"}, "unknown category: nope"),
        ({"area": "mars"}, "unknown area: mars"),
        ({"org": "hogwarts"}, "unknown org: hogwarts"),
        ({"start": "next tuesday"}, "start: expected"),
        ({"start": "2026-10-02", "end": "2026-10-01"}, "end must be after start"),
        ({"cursor": "garbage"}, "invalid cursor"),
    ],
)
def test_bad_params(client, params, message):
    resp = client.get("/events", params=params)
    assert resp.status_code == 422
    assert message in resp.json()["detail"]


def test_pagination(client):
    seen, cursor = [], None
    while True:
        resp = client.get("/events", params={"limit": 2, **({"cursor": cursor} if cursor else {})}).json()
        seen += [e["id"] for e in resp["events"]]
        if not (cursor := resp["next_cursor"]):
            break
    assert seen == ["today-all-day", "in-progress", "jazz", "lecture", "online"]


def test_duplicates_hidden_and_listed_on_canonical(client):
    jazz = next(e for e in client.get("/events").json()["events"] if e["id"] == "jazz")
    assert jazz["also_listed_by"] == [{"source": "test", "url": "https://other.edu/jazz"}]
    # Fetching the merged listing by id returns the canonical event.
    assert client.get("/events/jazz-copy").json()["id"] == "jazz"
    assert "Jazz Copy" not in {str(v["summary"]) for v in parse_ics(client.get("/events.ics"))}


def test_event_detail(client):
    body = client.get("/events/lecture").json()
    assert body["categories"] == ["talks"]
    assert body["location"] == {
        "name": "Kroon Hall", "room": "Burke Auditorium", "address": None, "id": "kroon-hall",
        "building": "Kroon Hall", "area": "science-hill", "lat": body["location"]["lat"],
        "lon": body["location"]["lon"], "virtual": False,
    }  # fmt: skip
    # Times come back in New Haven local time.
    assert datetime.fromisoformat(body["start"]).utcoffset() in (timedelta(hours=-4), timedelta(hours=-5))
    assert client.get("/events/missing").status_code == 404


def parse_ics(resp) -> list:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/calendar")
    return Calendar.from_ical(resp.content).walk("VEVENT")


def test_ics_feed(client):
    resp = client.get("/events.ics", params={"category": "talks"})
    vevents = {str(v["summary"]): v for v in parse_ics(resp)}
    # A week back, everything ahead, and cancelled events included so subscribers see the change.
    assert set(vevents) == {"Past", "In Progress", "Lecture", "Cancelled"}
    assert vevents["Cancelled"]["status"] == "CANCELLED"
    lecture = vevents["Lecture"]
    assert lecture["dtstart"].dt.tzinfo is not None
    assert lecture["dtend"].dt - lecture["dtstart"].dt == timedelta(hours=1)
    assert str(lecture["location"]) == "Burke Auditorium, Kroon Hall"
    assert str(lecture["uid"]) == "lecture@yale-events.local"
    assert b"X-WR-CALNAME:Yale Events: Talks & Lectures" in resp.content
    assert b"X-PUBLISHED-TTL:PT4H" in resp.content


def test_ics_all_day_uses_dates(client):
    (v,) = parse_ics(client.get("/events/today-all-day.ics"))
    assert v["dtstart"].dt == TODAY_MIDNIGHT.date()
    assert v["dtend"].dt == TODAY_MIDNIGHT.date() + timedelta(days=1)
    assert isinstance(v["dtstart"].dt, date) and not isinstance(v["dtstart"].dt, datetime)


def test_reference_endpoints(client):
    cats = {c["id"]: c["upcoming"] for c in client.get("/categories").json()}
    assert cats["talks"] == 1 and cats["music"] == 1 and cats["exhibitions"] == 0 and "film" in cats
    areas = {a["id"]: a["upcoming"] for a in client.get("/areas").json()}
    assert areas["science-hill"] == 1
    kroon = next(b for b in client.get("/locations", params={"area": "science-hill"}).json() if b["id"] == "kroon-hall")
    assert kroon["upcoming"] == 1
    orgs = {o["id"]: o for o in client.get("/orgs").json()}
    assert orgs["music"] == {"id": "music", "name": "School of Music", "kind": "department", "upcoming": 1}
    assert (orgs["arts"]["upcoming"], orgs["berkeley"]["kind"]) == (1, "college")
    (src,) = client.get("/sources").json()
    assert (src["id"], src["last_run_status"], src["upcoming"]) == ("test", "ok", 4)


def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<title>Yale Events</title>" in r.text
