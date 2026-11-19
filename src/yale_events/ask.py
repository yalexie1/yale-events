"""Plain-English event suggestions ("music tomorrow", "AI or CS career events in the next two weeks").

No vector index: Claude first turns the question into the same filters /events takes, `build_query`
fetches the candidates (so stale, duplicate, and finished events are excluded exactly as in the
feed), and a second call reads the candidates and picks the relevant ones. Upcoming events are short
and few enough (a few hundred in two weeks) to fit in one prompt.
"""

import logging
import os
import threading
import time as clock
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import anthropic
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from yale_events.api.query import EventFilters, build_query
from yale_events.models import Event
from yale_events.normalize import default_normalizer
from yale_events.normalize.time import NEW_HAVEN
from yale_events.orgs import default_orgs

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
MAX_QUESTION = 200
DEFAULT_DAYS = 14  # window when the question names no dates
MAX_DAYS = 90
MAX_CANDIDATES = 250  # ~100 tokens each
MAX_PICKS = 10
DESCRIPTION_CHARS = 300


# ---------- What the model returns ----------

class ParsedQuestion(BaseModel):
    start: str | None  # YYYY-MM-DD, New Haven
    end: str | None  # YYYY-MM-DD, inclusive
    categories: list[str]
    orgs: list[str]
    areas: list[str]
    free_food: bool


class Pick(BaseModel):
    id: str
    reason: str


class Picks(BaseModel):
    picks: list[Pick]


@dataclass
class Suggestion:
    event: Event
    reason: str


@dataclass
class Answer:
    filters: EventFilters
    suggestions: list[Suggestion]
    candidates: int  # how many events the ranker read


# ---------- Prompts ----------

def parse_system() -> str:
    """The filter vocabulary. Stable across requests, so it's cached."""
    n = default_normalizer()
    cats = "\n".join(f"- {k}: {v}" for k, v in n.categorizer.categories.items())
    areas = "\n".join(f"- {k}: {v}" for k, v in n.locations.areas.items())
    orgs = "\n".join(f"- {o.id}: {o.name} ({o.kind})" for o in default_orgs().values())
    return f"""You turn a request for Yale events into search filters.

Rules:
- start/end: the dates the request covers, as YYYY-MM-DD in New Haven time, end inclusive. "tonight" and \
"today" are today; "this weekend" is Friday through Sunday; "next two weeks" is today through 13 days \
from today. Leave both null when the request names no time.
- categories: only when the request is plainly about one of these kinds of event. Leave it empty for \
topics ("AI", "climate") that cut across categories.
- orgs: only when the request asks for events hosted by a college, school, department, or organization \
below ("Berkeley College events", "anything from the School of Music"). A subject is not a host: "CS \
careers" or "music events" name no org.
- areas: only when the request names a part of campus or "online".
- free_food: true only when the request asks for food.
Use only the ids listed below.

Categories:
{cats}

Areas:
{areas}

Organizations:
{orgs}"""


RANK_SYSTEM = f"""You recommend Yale events. You get a request and a list of candidate events, one per line:
id | when (New Haven time) | title | venue | categories | hosts | flags | description excerpt
"free food" in flags means the full listing offers food, even when the excerpt doesn't show it.

Pick up to {MAX_PICKS} events that best fit the request, best first. Judge by meaning, not shared words: \
a request about AI also fits a machine learning seminar or a data science talk. Respect any time of day in \
the request ("tonight", "morning"). Give each pick a reason of at most 15 words, written for the person \
asking, that says what the event offers them; don't repeat the title or time, and don't mention the \
list, the candidates, or other events. When the request has a requirement (food, online, a time of day, \
a host), pick only events whose details show they meet it; never pick an event while noting it may not \
fit. Use only ids from the list. Fewer picks are better than weak ones; if nothing fits, return no picks."""


def event_line(e: Event) -> str:
    start = e.start.astimezone(NEW_HAVEN)
    when = f"{start:%a %b %-d}" + ("" if e.all_day else f" {start:%-I:%M%p}".lower())
    venue = e.location_name or e.location_id or ("online" if e.virtual else "")
    desc = " ".join((e.description or "").split())[:DESCRIPTION_CHARS]
    flags = ", ".join(f for f, on in [("free food", e.free_food), ("online", e.virtual), ("free", e.free)] if on)
    return " | ".join([e.id, when, e.title, venue, ",".join(e.categories), "; ".join(e.groups or []), flags, desc])


# ---------- The pipeline ----------

class Asker:
    """Two Claude calls around one database query. Tests swap in a fake for `parse` and `rank`."""

    def __init__(self, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()

    def parse(self, question: str, today: date) -> ParsedQuestion | None:
        response = self.client.messages.parse(
            model=MODEL,
            max_tokens=2000,
            output_config={"effort": "low"},
            system=[{"type": "text", "text": parse_system(), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Today is {today:%A, %B %-d, %Y}.\nRequest: {question}"}],
            output_format=ParsedQuestion,
        )
        return response.parsed_output

    def rank(self, question: str, lines: list[str]) -> Picks | None:
        response = self.client.messages.parse(
            model=MODEL,
            max_tokens=4000,
            output_config={"effort": "low"},
            system=RANK_SYSTEM,
            messages=[{"role": "user", "content": f"Request: {question}\n\nCandidates:\n" + "\n".join(lines)}],
            output_format=Picks,
        )
        return response.parsed_output

    def ask(self, session: Session, question: str, now: datetime | None = None) -> Answer:
        now = now or datetime.now(UTC)
        parsed = self.parse(question, now.astimezone(NEW_HAVEN).date())
        f = to_filters(parsed, now)
        events = candidates(session, f)
        picks = self.rank(question, [event_line(e) for e in events]) if events else None
        by_id = {e.id: e for e in events}
        suggestions, seen = [], set()
        for p in picks.picks if picks else []:
            if p.id in by_id and p.id not in seen:  # drop invented or repeated ids
                seen.add(p.id)
                suggestions.append(Suggestion(by_id[p.id], p.reason.strip()))
        return Answer(f, suggestions[:MAX_PICKS], len(events))


def to_filters(parsed: ParsedQuestion | None, now: datetime) -> EventFilters:
    """Model output -> EventFilters, dropping unknown ids and clamping the window to [now, now + MAX_DAYS]."""
    p = parsed or ParsedQuestion(start=None, end=None, categories=[], orgs=[], areas=[], free_food=False)
    n, orgs = default_normalizer(), default_orgs()

    def day(s: str | None) -> date | None:
        try:
            return date.fromisoformat(s) if s else None
        except ValueError:
            return None

    today = now.astimezone(NEW_HAVEN).date()
    first, last = day(p.start), day(p.end)
    start = now if not first or first <= today else datetime.combine(first, time(), NEW_HAVEN)
    end = datetime.combine(last + timedelta(days=1), time(), NEW_HAVEN) if last else start + timedelta(days=DEFAULT_DAYS)
    end = min(max(end, start + timedelta(hours=1)), now + timedelta(days=MAX_DAYS))
    return EventFilters(
        start=start,
        end=end,
        category=[c for c in p.categories if c in n.categorizer.categories],
        org=[o for o in p.orgs if o in orgs],
        area=[a for a in p.areas if a in n.locations.areas],
        free_food=True if p.free_food else None,
    )


def candidates(session: Session, f: EventFilters) -> list[Event]:
    """Events the ranker reads. Dates, areas, and free food are hard filters. Categories and orgs only
    put matching events first when the window holds more than MAX_CANDIDATES: the keyword rules miss
    many events, and the model sometimes reads a subject ("CS careers") as a host. If the hard filters
    leave nothing, the dates alone apply and the ranker reads the text (the free-food rules miss some)."""
    preferred = select(Event.id).where(build_query(EventFilters(category=f.category, org=f.org)).whereclause)

    def fetch(hard: EventFilters) -> list[Event]:
        stmt = build_query(hard)
        if f.category or f.org:
            stmt = stmt.order_by(None).order_by(Event.id.in_(preferred).desc(), Event.start, Event.id)
        return list(session.scalars(stmt.limit(MAX_CANDIDATES)))

    events = fetch(EventFilters(start=f.start, end=f.end, area=f.area, free_food=f.free_food))
    if not events and (f.area or f.free_food):
        events = fetch(EventFilters(start=f.start, end=f.end))
    return sorted(events, key=lambda e: (e.start, e.id))


# ---------- Guards ----------

class RateLimiter:
    """At most `per_hour` asks per visitor and `per_day` overall, in memory (one machine)."""

    def __init__(self, per_hour: int, per_day: int):
        self.per_hour, self.per_day = per_hour, per_day
        self.visitors: dict[str, deque[float]] = {}
        self.day: tuple[date, int] = (date.min, 0)
        self.lock = threading.Lock()

    def allow(self, visitor: str, now: float | None = None) -> str | None:
        """None if allowed, else why not."""
        now = clock.time() if now is None else now
        today = datetime.now(NEW_HAVEN).date()
        with self.lock:
            if self.day[0] != today:
                self.day = (today, 0)
                self.visitors.clear()
            if self.day[1] >= self.per_day:
                return "day"
            q = self.visitors.setdefault(visitor, deque())
            while q and q[0] <= now - 3600:
                q.popleft()
            if len(q) >= self.per_hour:
                return "visitor"
            q.append(now)
            self.day = (today, self.day[1] + 1)
            return None


class AnswerCache:
    """Recent answers by question, so a repeated or shared question doesn't call the API again."""

    def __init__(self, size: int = 256, ttl: float = 1800):
        self.size, self.ttl = size, ttl
        self.items: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self.lock = threading.Lock()

    @staticmethod
    def key(question: str) -> str:
        return f"{datetime.now(NEW_HAVEN).date()}:{' '.join(question.lower().split())}"

    def get(self, question: str):
        with self.lock:
            hit = self.items.get(self.key(question))
            return hit[1] if hit and clock.time() - hit[0] < self.ttl else None

    def put(self, question: str, value) -> None:
        k = self.key(question)
        with self.lock:
            self.items[k] = (clock.time(), value)
            self.items.move_to_end(k)
            while len(self.items) > self.size:
                self.items.popitem(last=False)


def default_asker() -> Asker | None:
    """An Asker when ANTHROPIC_API_KEY is set; /ask answers 503 otherwise."""
    return Asker() if os.environ.get("ANTHROPIC_API_KEY") else None


def default_limiter() -> RateLimiter:
    return RateLimiter(
        per_hour=int(os.environ.get("YEV_ASK_PER_HOUR", "10")),
        per_day=int(os.environ.get("YEV_ASK_PER_DAY", "500")),
    )
