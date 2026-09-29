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
    return make_client(make_events())


def make_client(events: list[Event]) -> TestClient:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        s.add(Source(id="test", name="Test", type="fake", url="https://example.edu"))
        s.add(ScrapeRun(source_id="test", status="ok", fetched=10))
        s.add_all(events)
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
        ({"only_ongoing": "true"}, ["exhibit"]),
        ({"include_cancelled": "true", "category": "talks"}, ["in-progress", "lecture", "cancelled"]),
        ({"source": "other"}, []),
        ({"org": "music"}, ["jazz"]),  # by group
        ({"org": "music,environment"}, ["jazz", "lecture"]),
        ({"org": "berkeley"}, []),  # by building only
    ],
)
def test_filters(client, params, expected):
    assert ids(client.get("/events", params=params)) == expected


@pytest.mark.parametrize(
    "q, expected",
    [
        ("YPU", ["debate"]),  # shorthand -> the host group's full name
        ("political union debate", ["debate"]),  # every word, in any field
        ("school of management", ["mba"]),  # full name -> shorthand
        ("som", ["mba"]),  # shorthands match whole words: not "Some Things"
        ("HQ", ["debate"]),  # building alias -> events held there
        ("GH", []),  # building shorthands match whole words too: not "Things"
        ("jazz pizza", ["jazz"]),
        ("100%", []),  # LIKE wildcards are literal
    ],
)
def test_search(q, expected):
    client = make_client([
        *make_events(),
        ev("debate", NOW + timedelta(days=5), ["talks"], location_id="humanities-quadrangle",
           title="Debate: Resolved, Some Things Are Worth It", groups=["The Yale Political Union (Undergraduate)"]),
        ev("mba", NOW + timedelta(days=6), ["career"], title="SOM Admissions Info Session"),
    ])  # fmt: skip
    assert ids(client.get("/events", params={"q": q})) == expected


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
        "lon": body["location"]["lon"], "virtual": False, "online": None, "sign_in_url": None,
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
    assert cats["talks"] == 2 and cats["music"] == 1 and cats["exhibitions"] == 0 and cats["film"] == 0
    areas = {a["id"]: a["upcoming"] for a in client.get("/areas").json()}
    assert areas["science-hill"] == 1
    kroon = next(b for b in client.get("/locations", params={"area": "science-hill"}).json() if b["id"] == "kroon-hall")
    assert kroon["upcoming"] == 1
    orgs = {o["id"]: o for o in client.get("/orgs").json()}
    assert orgs["music"] == {"id": "music", "name": "School of Music", "kind": "school", "upcoming": 1}
    assert (orgs["arts"]["upcoming"], orgs["berkeley"]["kind"]) == (1, "college")
    (src,) = client.get("/sources").json()
    assert (src["id"], src["last_run_status"], src["upcoming"]) == ("test", "ok", 5)


@pytest.mark.parametrize("window", [{}, {"end": (NOW + timedelta(days=1)).date().isoformat()},
                                    {"start": (NOW + timedelta(days=100)).date().isoformat()}])  # fmt: skip
def test_counts_match_what_each_filter_lists(client, window):
    for path, kind in [("/categories", "category"), ("/areas", "area"), ("/orgs", "org")]:
        for item in client.get(path, params=window).json():
            listed = ids(client.get("/events", params={**window, kind: item["id"], "limit": 500}))
            assert item["upcoming"] == len(listed), (kind, item["id"], window)


def test_health(client):
    r = client.get("/health")  # sources.yaml's sources have no runs here: warnings, not errors
    assert r.status_code == 200 and r.json()["ok"] and {p["level"] for p in r.json()["problems"]} == {"warning"}


def test_hidden_location_links_to_the_event_page():
    from yale_events.adapters.ical import HIDDEN_LOCATION_LABEL

    url = "https://yaleconnect.yale.edu/rsvp?id=1"
    c = make_client([ev("club", NOW + timedelta(days=1), location_name=HIDDEN_LOCATION_LABEL, url=url),
                     ev("hall", NOW + timedelta(days=2), location_name="Kroon Hall", url="https://x.edu")])  # fmt: skip
    locs = {e["id"]: e["location"] for e in c.get("/events").json()["events"]}
    assert locs["club"]["sign_in_url"] == url and locs["hall"]["sign_in_url"] is None
    club = next(v for v in Calendar.from_ical(c.get("/events.ics").content).walk("VEVENT") if "Club" in str(v["SUMMARY"]))
    assert str(club["DESCRIPTION"]).startswith(f"Location: sign in at {url}")


def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<title>Yale Events</title>" in r.text


def test_online_access_kept_out_of_the_venue():
    teams = "Join: https://teams.microsoft.com/meet/123?p=abc Meeting ID: 123 Passcode: Tq6k Also in-person, FMP 132"
    client = make_client([
        ev("hybrid", NOW + timedelta(days=1), location_name=teams, virtual=True),
        ev("label", NOW + timedelta(days=2), location_name="Online Event"),
    ])  # fmt: skip
    hybrid = client.get("/events/hybrid").json()["location"]
    assert hybrid["name"] == "FMP 132"
    assert hybrid["virtual"] is True
    assert hybrid["online"] == {"url": "https://teams.microsoft.com/meet/123?p=abc",
                                "details": "Join: https://teams.microsoft.com/meet/123?p=abc Meeting ID: 123 Passcode: Tq6k"}
    label = client.get("/events/label").json()["location"]
    assert (label["name"], label["virtual"], label["online"]) == (None, True, None)
    vevents = {str(v["uid"]).split("@")[0]: v for v in parse_ics(client.get("/events.ics"))}
    assert str(vevents["hybrid"]["location"]) == "FMP 132"
    assert "Passcode: Tq6k" in str(vevents["hybrid"]["description"])
    assert str(vevents["label"]["location"]) == "Online"


def test_sort_by_added():
    client = make_client([
        ev(f"e{i}", NOW + timedelta(days=i), first_seen=NOW - timedelta(days=10 - i)) for i in range(1, 6)
    ])  # fmt: skip
    first = client.get("/events", params={"sort": "added", "limit": 2}).json()
    assert [e["id"] for e in first["events"]] == ["e5", "e4"]
    rest = client.get("/events", params={"sort": "added", "limit": 10, "cursor": first["next_cursor"]})
    assert ids(rest) == ["e3", "e2", "e1"]
    # One scrape's batch shares first_seen; it pages soonest first.
    batch = make_client([ev(f"b{i}", NOW + timedelta(days=6 - i), first_seen=NOW) for i in range(1, 6)])
    page = batch.get("/events", params={"sort": "added", "limit": 3}).json()
    rest = batch.get("/events", params={"sort": "added", "cursor": page["next_cursor"]})
    assert [e["id"] for e in page["events"]] + ids(rest) == ["b5", "b4", "b3", "b2", "b1"]
    assert client.get("/events", params={"sort": "nope"}).status_code == 422


def test_days_counts_each_start_date(client):
    start = datetime.now(NEW_HAVEN).date()
    days = client.get("/days", params={"start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat()}).json()
    assert [d["date"] for d in days] == [(start + timedelta(days=i)).isoformat() for i in range(7)]
    listed = ids(client.get("/events", params={"start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat()}))
    assert sum(d["events"] for d in days) == len(listed)
    # Events already underway when the window opens aren't counted on any day.
    params = {"start": start.isoformat(), "category": "talks"}
    talks = client.get("/events", params=params).json()["events"]
    started = [e for e in talks if datetime.fromisoformat(e["start"]).date() >= start]
    assert sum(d["events"] for d in client.get("/days", params=params).json()) == len(started) >= 1
