"""Cross-source duplicate detection.

Two events from different sources are the same event when they start within MAX_START_GAP of each
other, are both all-day or both timed, have similar titles, and aren't placed in different buildings.
Events in the same building at the same time need less: one shared distinctive title word, since
sources title the same event differently ("Tenebrae" / "The Journey: 25 Years of Tenebrae").
Each group keeps one canonical event (the earliest source in sources.yaml wins, as sources are listed
richest first); the rest get `duplicate_of` set to it.
"""

import re
from datetime import datetime, timedelta

from rapidfuzz import fuzz
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from yale_events.models import Event

MAX_START_GAP = timedelta(minutes=15)
SORT_RATIO = 85  # word-order-insensitive similarity of the whole titles
SUBSET_RATIO = 95  # one title contained in the other ("Senior Toast" / "Senior Toast in the Courtyard")
SUBSET_MIN_WORDS = 3
SAME_SLOT = timedelta(minutes=5)
# Words that say what kind of event it is rather than which one; they don't count as shared.
GENERIC_WORDS = set("""
    film films series seminar seminars lecture lectures talk talks concert concerts conversation event events
    program programs presents presented night workshop meeting session sessions class day week weekend
    annual center college school university department reception open house tour info information
    with and for from into about women men mens womens vs versus fall spring winter summer new haven
    """.split())  # fmt: skip


def norm_title(title: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", title.lower().replace("&", " and "))
    return " ".join(w for w in t.split() if w not in {"the", "a", "an", "yale"})


def distinctive(norm: str) -> set[str]:
    return {w for w in norm.split() if len(w) >= 4 and w not in GENERIC_WORDS}


def same_event(a: Event, b: Event) -> bool:
    if a.source_id == b.source_id or a.all_day != b.all_day:
        return False
    if abs(a.start - b.start) > MAX_START_GAP:
        return False
    if a.location_id and b.location_id and a.location_id != b.location_id:
        return False
    ta, tb = norm_title(a.title), norm_title(b.title)
    if fuzz.token_sort_ratio(ta, tb) >= SORT_RATIO:
        return True
    if a.location_id and a.location_id == b.location_id and abs(a.start - b.start) <= SAME_SLOT:
        if distinctive(ta) & distinctive(tb):
            return True
    shorter = min(ta, tb, key=len)
    return len(shorter.split()) >= SUBSET_MIN_WORDS and fuzz.token_set_ratio(ta, tb) >= SUBSET_RATIO


def dedupe(session: Session, source_priority: list[str], since: datetime) -> int:
    """Recompute duplicate_of for live events starting from `since`. Returns how many are duplicates."""
    events = list(
        session.scalars(select(Event).where(Event.stale.is_(False), Event.start >= since).order_by(Event.start))
    )
    parent = {e.id: e.id for e in events}
    sources = {e.id: {e.source_id} for e in events}  # per group root, so a group never holds two events of one source

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(events):
        for b in events[i + 1 :]:
            if b.start - a.start > MAX_START_GAP:
                break
            ra, rb = find(a.id), find(b.id)
            if ra != rb and not (sources[ra] & sources[rb]) and same_event(a, b):
                parent[ra] = rb
                sources[rb] |= sources.pop(ra)

    groups: dict[str, list[Event]] = {}
    for e in events:
        groups.setdefault(find(e.id), []).append(e)

    rank = {s: i for i, s in enumerate(source_priority)}
    session.execute(
        update(Event).where(Event.start >= since, Event.duplicate_of.is_not(None)).values(duplicate_of=None)
    )
    marked = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        canonical = min(members, key=lambda e: (rank.get(e.source_id, len(rank)), -len(e.description or ""), e.id))
        for e in members:
            if e is not canonical:
                e.duplicate_of = canonical.id
                marked += 1
    session.commit()
    return marked
