from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.test_api import make_client, make_events
from yale_events.models import Hit
from yale_events.normalize.time import NEW_HAVEN
from yale_events.stats import build_report, feed_client, format_report, is_bot, subscription_keys

BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"


@pytest.fixture
def client():
    c = make_client(make_events())
    c.headers["user-agent"] = BROWSER
    return c


def hits(client) -> list[Hit]:
    with client.app.state.session_factory() as s:
        return list(s.scalars(select(Hit).order_by(Hit.id)))


def test_hits_recorded(client):
    jazz = client.get("/events", params={"q": "jazz"}).json()["events"][0]["id"]
    assert client.get("/").status_code == 200
    assert client.post("/track", params={"kind": "view", "event": jazz}).status_code == 204
    assert client.post("/track", params={"kind": "gcal", "event": jazz}).status_code == 204
    assert client.get(f"/events/{jazz}.ics").status_code == 200
    assert client.get("/events.ics?category=talks&category=music", headers={"user-agent": "Google-Calendar-Importer"}).status_code == 200
    assert client.get("/events.ics", headers={"fly-client-ip": "1.2.3.4", "user-agent": "iOS/17.0 (21A329) dataaccessd/1.0"}).status_code == 200
    rows = hits(client)
    assert [(h.kind, h.event_id) for h in rows] == [
        ("page", None), ("view", jazz), ("gcal", jazz), ("ics", jazz), ("feed", None), ("feed", None),
    ]  # fmt: skip
    assert rows[4].client == "google" and rows[4].query == "category=music&category=talks"
    assert rows[5].client == "apple" and rows[5].query == ""
    assert rows[0].visitor == rows[1].visitor != rows[5].visitor
    assert all(len(h.visitor) == 16 and "1.2.3.4" not in h.visitor for h in rows)


def test_track_rejects_unknown(client):
    assert client.post("/track", params={"kind": "page", "event": "0" * 16}).status_code == 400
    assert client.post("/track", params={"kind": "view", "event": "no/pe"}).status_code == 400
    assert hits(client) == []


def test_bots_not_counted_except_feeds(client):
    client.get("/", headers={"user-agent": "Googlebot/2.1"})
    client.get("/", headers={"user-agent": ""})
    assert hits(client) == []


def test_goatcounter(monkeypatch, client):
    from yale_events.api import main

    main.index_html.cache_clear()
    assert "goatcounter" not in client.get("/").text
    monkeypatch.setenv("YEV_GOATCOUNTER", "yale-events")
    main.index_html.cache_clear()
    assert 'data-goatcounter="https://yale-events.goatcounter.com/count"' in client.get("/").text
    main.index_html.cache_clear()


@pytest.mark.parametrize("ua, expected", [
    ("Google-Calendar-Importer", "google"),
    ("macOS/14.5 (23F79) CalendarAgent/988", "apple"),
    ("iOS/17.0 (21A329) dataaccessd/1.0", "apple"),
    ("Microsoft Office/16.0 (Windows NT 10.0; Microsoft Outlook 16.0.17328; Pro)", "outlook"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:115.0) Gecko/20100101 Thunderbird/115.0", "thunderbird"),
    (BROWSER, "browser"),
    ("", "other"),
])  # fmt: skip
def test_feed_client(ua, expected):
    assert feed_client(ua) == expected


def test_is_bot():
    assert is_bot("Mozilla/5.0 (compatible; bingbot/2.0)") and is_bot("python-httpx/0.27") and not is_bot(BROWSER)


def test_subscription_keys():
    feed = lambda client, visitor, query: Hit(kind="feed", client=client, visitor=visitor, query=query)  # noqa: E731
    keys = subscription_keys([
        feed("google", "a", "category=music"), feed("google", "b", "category=music"),  # one Google subscription
        feed("apple", "c", ""), feed("apple", "c", ""), feed("apple", "d", ""),  # two devices
        feed("browser", "e", ""),  # a download
    ])  # fmt: skip
    assert len(keys) == 3


def test_report(client):
    jazz = client.get("/events", params={"q": "jazz"}).json()["events"][0]["id"]
    client.get("/")
    client.post("/track", params={"kind": "view", "event": jazz})
    client.post("/track", params={"kind": "view", "event": jazz}, headers={"fly-client-ip": "5.6.7.8"})
    client.get(f"/events/{jazz}.ics")
    client.get("/events.ics?org=music", headers={"user-agent": "Google-Calendar-Importer"})
    with client.app.state.session_factory() as s:
        s.add(Hit(kind="page", visitor="old", at=datetime.now(NEW_HAVEN) - timedelta(days=30)))
        s.commit()
        r = build_report(s, days=7)
    today = r.days[-1]
    assert today == datetime.now(NEW_HAVEN).date() and len(r.days) == 7
    assert r.daily[today]["visitors"] == 2 and r.daily[today]["view"] == 2 and r.daily[today]["page"] == 1
    assert r.daily[today]["subscriptions"] == 1
    assert sum(r.daily[d]["page"] for d in r.days) == 1  # the 30-day-old hit is outside the window
    assert r.top_viewed[0][1] == 2 and r.top_added[0][1] == 1
    assert dict(r.subscriptions) == {"google": 1} and r.feeds == [("org=music", 1)]
    text = format_report(r)
    assert "Most viewed events" in text and "org=music" in text


def test_report_empty(client):
    with client.app.state.session_factory() as s:
        r = build_report(s, days=3, today=date(2026, 1, 1))
    assert r.days == [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 1)] and not r.subscriptions
    format_report(r)
