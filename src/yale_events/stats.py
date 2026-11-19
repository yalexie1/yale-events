"""Visitor counts for `yev stats`: page loads, event views, calendar adds, and calendar subscriptions.

Hits are recorded by the API (see `record`); page analytics with referrers and countries come from
GoatCounter when `YEV_GOATCOUNTER` is set. Nothing here stores an IP address."""

import hashlib
import logging
import os
import re
import secrets
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from yale_events.models import Event, Hit
from yale_events.normalize.time import NEW_HAVEN

log = logging.getLogger(__name__)

KINDS = {
    "page": "page loads of /",
    "view": "event details opened",
    "link": "clicks through to the event's own page",
    "ics": "single-event .ics downloads (Add to calendar)",
    "gcal": "Add to Google Calendar clicks",
    "ask": "questions answered by /ask (plain-English suggestions)",
    "feed": "fetches of /events.ics (subscriptions and one-time downloads)",
}
# Set YEV_STATS_SALT (fly secrets) so a restart doesn't count the day's visitors again.
SALT = os.environ.get("YEV_STATS_SALT") or secrets.token_hex(16)
BOT = re.compile(r"bot|crawl|spider|slurp|preview|monitor|headless|curl|wget|python|httpx|go-http|uptime", re.I)
FEED_CLIENTS = [  # first match wins
    ("google", re.compile(r"google", re.I)),
    ("apple", re.compile(r"CalendarAgent|dataaccessd|iCal|Mac ?OS X.*Calendar", re.I)),
    ("outlook", re.compile(r"Microsoft|Outlook", re.I)),
    ("thunderbird", re.compile(r"Thunderbird", re.I)),
    ("browser", re.compile(r"Mozilla", re.I)),  # a one-time download, not a subscription
]


def visitor_hash(ip: str, user_agent: str, day: date) -> str:
    return hashlib.sha256(f"{SALT}:{day}:{ip}:{user_agent}".encode()).hexdigest()[:16]


def is_bot(user_agent: str) -> bool:
    return not user_agent or bool(BOT.search(user_agent))


def feed_client(user_agent: str) -> str:
    return next((name for name, rx in FEED_CLIENTS if rx.search(user_agent)), "other")


def feed_query(params: list[tuple[str, str]]) -> str:
    """The filters of a feed URL in a stable order, so the same subscription always reads the same."""
    return urlencode(sorted((k, v) for k, v in params if v))


def record(
    factory: sessionmaker[Session], kind: str, ip: str, user_agent: str, event_id: str | None = None,
    query: str | None = None,
) -> None:
    """Store one hit. Runs after the response is sent; a failure is logged, never raised."""
    if kind != "feed" and is_bot(user_agent):
        return
    now = datetime.now(NEW_HAVEN)
    hit = Hit(kind=kind, event_id=event_id, visitor=visitor_hash(ip, user_agent, now.date()))
    if kind == "feed":
        hit.client, hit.query = feed_client(user_agent), query
    try:
        with factory() as session:
            session.add(hit)
            session.commit()
    except Exception:
        log.exception("couldn't record %s hit", kind)


@dataclass
class Report:
    days: list[date]
    # day -> kind -> count; "visitors" counts distinct visitors across page loads and event views.
    daily: dict[date, Counter] = field(default_factory=dict)
    top_viewed: list[tuple[str, int]] = field(default_factory=list)  # (title, count)
    top_added: list[tuple[str, int]] = field(default_factory=list)
    subscriptions: Counter = field(default_factory=Counter)  # client -> calendars on the last day with feed fetches
    feeds: list[tuple[str, int]] = field(default_factory=list)  # (query, calendars) over the same day


def subscription_keys(hits: list[Hit]) -> set[tuple]:
    """Distinct subscribed calendars among one day's feed fetches. Google fetches from many addresses,
    so each Google-subscribed URL counts once; other apps count once per device (visitor) and URL.
    Browsers are downloads, not subscriptions."""
    return {
        (h.client, h.query) if h.client == "google" else (h.client, h.visitor, h.query)
        for h in hits
        if h.client != "browser"
    }


def build_report(session: Session, days: int, today: date | None = None, top: int = 10) -> Report:
    today = today or datetime.now(NEW_HAVEN).date()
    first = today - timedelta(days=days - 1)
    since = datetime.combine(first, datetime.min.time(), NEW_HAVEN)
    hits = list(session.scalars(select(Hit).where(Hit.at >= since).order_by(Hit.at)))
    report = Report(days=[first + timedelta(days=i) for i in range(days)])
    by_day: dict[date, list[Hit]] = {d: [] for d in report.days}
    for h in hits:
        by_day.setdefault(h.at.astimezone(NEW_HAVEN).date(), []).append(h)
    for d, day_hits in by_day.items():
        c = Counter(h.kind for h in day_hits)
        c["visitors"] = len({h.visitor for h in day_hits if h.kind in ("page", "view", "link", "ics", "gcal", "ask")})
        c["subscriptions"] = len(subscription_keys([h for h in day_hits if h.kind == "feed"]))
        report.daily[d] = c

    titles = dict(session.execute(
        select(Event.id, Event.title).where(Event.id.in_({h.event_id for h in hits if h.event_id}))
    ).all())  # fmt: skip

    def ranked(kinds: tuple[str, ...]) -> list[tuple[str, int]]:
        counts = Counter(h.event_id for h in hits if h.kind in kinds and h.event_id)
        return [(titles.get(i, f"(gone) {i}"), n) for i, n in counts.most_common(top)]

    report.top_viewed, report.top_added = ranked(("view",)), ranked(("ics", "gcal"))
    feed_days = [d for d in report.days if any(h.kind == "feed" for h in by_day[d])]
    if feed_days:
        keys = subscription_keys([h for h in by_day[feed_days[-1]] if h.kind == "feed"])
        report.subscriptions = Counter(k[0] for k in keys)
        report.feeds = Counter(k[-1] or "(all events)" for k in keys).most_common(top)
    return report


def format_report(r: Report) -> str:
    cols = [("visitors", "visitors"), ("page", "pages"), ("view", "views"), ("link", "clicks"),
            ("ics", ".ics"), ("gcal", "google"), ("ask", "asks"), ("subscriptions", "subscribed")]  # fmt: skip
    lines = ["date        " + " ".join(f"{h:>10}" for _, h in cols)]
    for d in r.days:
        lines.append(f"{d:%a %b %d}  " + " ".join(f"{r.daily[d][k]:>10}" for k, _ in cols))
    total = sum((r.daily[d] for d in r.days), Counter())
    lines.append("total       " + " ".join(f"{'' if k in ('visitors', 'subscriptions') else total[k]:>10}" for k, _ in cols))
    lines += [
        "",
        "visitors: distinct people that day (page loads, views, adds); views: event details opened;",
        "clicks: through to the source's event page; .ics/google: Add to calendar; asks: questions to /ask;",
        "subscribed: calendars that fetched a feed that day (Google counts once per feed URL).",
    ]
    for heading, rows in (("Most viewed events", r.top_viewed), ("Most added to calendars", r.top_added)):
        if rows:
            lines += ["", heading] + [f"{n:>5}  {t}" for t, n in rows]
    if r.subscriptions:
        lines += ["", "Subscribed calendars by app: " + ", ".join(f"{c} {n}" for c, n in r.subscriptions.most_common())]
        lines += ["Subscribed feeds"] + [f"{n:>5}  {q}" for q, n in r.feeds]
    return "\n".join(lines)

