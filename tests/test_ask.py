from datetime import UTC, datetime, timedelta

import anthropic
import httpx2
import pytest

from tests.test_api import NOW, make_client, make_events
from yale_events.ask import (
    AnswerCache, Asker, ParsedQuestion, Pick, Picks, RateLimiter, event_line, to_filters,
)  # fmt: skip
from yale_events.normalize.time import NEW_HAVEN


class FakeAsker(Asker):
    """Asker with canned model replies; records what the ranker was shown."""

    def __init__(self, parsed: ParsedQuestion | None, picks: list[tuple[str, str]], error: Exception | None = None):
        self.parsed, self.picks, self.error = parsed, picks, error
        self.shown: list[str] = []
        self.calls = 0

    def parse(self, question, today):
        self.calls += 1
        if self.error:
            raise self.error
        return self.parsed

    def rank(self, question, lines):
        self.shown = [line.split(" | ")[0] for line in lines]
        return Picks(picks=[Pick(id=i, reason=r) for i, r in self.picks])


def parsed(**kw) -> ParsedQuestion:
    return ParsedQuestion(**{"start": None, "end": None, "categories": [], "orgs": [], "areas": [], "free_food": False, **kw})


def ask_client(asker):
    c = make_client(make_events())
    c.app.state.asker = asker
    return c


def test_ask_returns_ranked_events_with_reasons():
    asker = FakeAsker(parsed(categories=["music"]), [("jazz", "A jazz concert."), ("lecture", "A talk.")])
    r = ask_client(asker).post("/ask", json={"q": "music this week"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["id"] for e in body["events"]] == ["jazz", "lecture"]
    assert body["events"][0]["reason"] == "A jazz concert."
    assert body["events"][0]["title"] == "Jazz"
    assert body["filters"]["category"] == ["music"]
    # Categories are a preference: other events in the window are still shown to the ranker.
    assert {"jazz", "lecture", "online"} <= set(asker.shown)
    assert not {"past", "stale", "cancelled", "jazz-copy", "far", "exhibit"} & set(asker.shown)


def test_ask_drops_invented_and_repeated_ids():
    asker = FakeAsker(parsed(), [("nope", "?"), ("jazz", "a"), ("jazz", "b"), ("far", "outside the window")])
    body = ask_client(asker).post("/ask", json={"q": "anything"}).json()
    assert [e["id"] for e in body["events"]] == ["jazz"]


def test_ask_hard_filters_and_dates():
    tomorrow = (NOW.astimezone(NEW_HAVEN) + timedelta(days=4)).date().isoformat()
    asker = FakeAsker(parsed(start=tomorrow, end=tomorrow, areas=["online"]), [])
    body = ask_client(asker).post("/ask", json={"q": "online things"}).json()
    assert asker.shown == ["online"]
    assert body["filters"]["start"] == tomorrow and body["filters"]["end"] == tomorrow


def test_to_filters_drops_unknown_ids_and_clamps():
    now = datetime(2026, 11, 18, 15, tzinfo=UTC)
    f = to_filters(parsed(start="2020-01-01", end="2030-01-01", categories=["music", "bogus"], orgs=["nope"]), now)
    assert f.start == now  # past dates start now, so finished events stay out
    assert f.end == now + timedelta(days=90)
    assert f.category == ["music"] and f.org == []
    default = to_filters(None, now)
    assert default.end - default.start == timedelta(days=14)
    assert to_filters(parsed(start="garbage"), now).start == now


def test_ask_errors():
    c = make_client(make_events())
    c.app.state.asker = None
    assert c.post("/ask", json={"q": "music"}).status_code == 503
    c.app.state.asker = FakeAsker(parsed(), [])
    assert c.post("/ask", json={"q": "   "}).status_code == 422
    assert c.post("/ask", json={"q": "x" * 201}).status_code == 422
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    c.app.state.asker = FakeAsker(None, [], error=anthropic.APIConnectionError(request=req))
    assert c.post("/ask", json={"q": "music"}).status_code == 502


def test_ask_rate_limit_and_cache():
    asker = FakeAsker(parsed(), [("jazz", "a")])
    c = ask_client(asker)
    c.app.state.ask_limiter = RateLimiter(per_hour=2, per_day=100)
    assert c.post("/ask", json={"q": "one"}).status_code == 200
    assert c.post("/ask", json={"q": "One "}).status_code == 200  # same question: cached, not counted
    assert asker.calls == 1
    assert c.post("/ask", json={"q": "two"}).status_code == 200
    assert c.post("/ask", json={"q": "three"}).status_code == 429


def test_rate_limiter_windows():
    rl = RateLimiter(per_hour=1, per_day=2)
    assert rl.allow("a", now=0) is None
    assert rl.allow("a", now=10) == "visitor"
    assert rl.allow("a", now=3601) is None
    assert rl.allow("b", now=3602) == "day"


def test_answer_cache_expires():
    cache = AnswerCache(size=1, ttl=60)
    cache.put("Music  tomorrow", "x")
    assert cache.get("music tomorrow") == "x"
    cache.put("other", "y")
    assert cache.get("music tomorrow") is None  # evicted


def test_event_line():
    e = make_events()[0]
    line = event_line(e)
    assert line.startswith("jazz | ") and "Jazz" in line and "music" in line and "Pizza after the show." in line


def test_ask_orgs_are_soft_and_thin_hard_filters_fall_back():
    # An org the events don't match still leaves the window's events for the ranker.
    asker = FakeAsker(parsed(orgs=["computer-science"], categories=["career"]), [])
    ask_client(asker).post("/ask", json={"q": "CS careers"})
    assert {"jazz", "lecture", "online"} <= set(asker.shown)
    # Free food: only the matching event, while one matches...
    asker = FakeAsker(parsed(free_food=True), [])
    ask_client(asker).post("/ask", json={"q": "food"})
    assert asker.shown == ["jazz"]
    # ...and the dates alone when none does, so the ranker can read the text.
    asker = FakeAsker(parsed(free_food=True, areas=["online"]), [])
    ask_client(asker).post("/ask", json={"q": "online food"})
    assert {"jazz", "lecture", "online"} <= set(asker.shown)
