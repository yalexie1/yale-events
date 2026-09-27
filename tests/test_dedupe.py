from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from yale_events.db import make_session_factory
from yale_events.dedupe import dedupe, norm_title, same_event
from yale_events.models import Event, Source

T = datetime(2026, 10, 5, 20, 30, tzinfo=UTC)
PRIORITY = ["yale-central", "law", "engineering"]


def ev(id_, source, title, start=T, **kw) -> Event:
    return Event(id=id_, source_id=source, source_event_id=id_, title=title, start=start, **kw)


@pytest.mark.parametrize(
    "a, b, expected",
    [
        # Curly vs straight apostrophe, same time.
        (ev("1", "law", "2026 Fall Gruber Distinguished Lecture in Women’s Rights"),
         ev("2", "yale-central", "2026 Fall Gruber Distinguished Lecture in Women's Rights"), True),
        # One title contains the other.
        (ev("1", "engineering", "Dean's Invited Speaker Series featuring Jim McNerney"),
         ev("2", "yale-central", "Yale Engineering Dean's Invited Speaker Series featuring Jim McNerney '71"), True),
        (ev("1", "law", "Lecture"), ev("2", "yale-central", "Lecture", start=T + timedelta(minutes=10)), True),
        # Too far apart in time.
        (ev("1", "law", "Lecture"), ev("2", "yale-central", "Lecture", start=T + timedelta(minutes=30)), False),
        # Same source never merges.
        (ev("1", "law", "Lecture"), ev("2", "law", "Lecture"), False),
        # Different buildings.
        (ev("1", "law", "Lecture", location_id="law-school"),
         ev("2", "yale-central", "Lecture", location_id="kroon-hall"), False),
        # Similar series titles, different speakers.
        (ev("1", "engineering", "ME Seminar Series: Cari Dutcher"),
         ev("2", "yale-central", "MCDB Seminar Series: Zev Gartner"), False),
        # Same building and time: one shared distinctive word is enough.
        (ev("1", "music", "Tenebrae", location_id="woolsey-hall"),
         ev("2", "yale-central", "The Journey: 25 Years of Tenebrae", location_id="woolsey-hall"), True),
        (ev("1", "yale-connect", "Sundance Revisited Film Series: BRICK", location_id="hq"),
         ev("2", "yale-central", "Film: Brick", location_id="hq", start=T + timedelta(minutes=5)), True),
        # ...but not generic words, a different building, or a start more than a few minutes apart.
        (ev("1", "yale-connect", "Film Series: Gun Crazy", location_id="hq"),
         ev("2", "yale-central", "Film: Brick", location_id="hq"), False),
        (ev("1", "music", "Tenebrae", location_id="woolsey-hall"),
         ev("2", "yale-central", "The Journey: 25 Years of Tenebrae"), False),
        (ev("1", "music", "Tenebrae", location_id="woolsey-hall"),
         ev("2", "yale-central", "The Journey: 25 Years of Tenebrae", location_id="woolsey-hall",
            start=T + timedelta(minutes=15)), False),
        # Short titles need a near-exact match, not just containment.
        (ev("1", "law", "Yoga"), ev("2", "yale-central", "Yoga at Payne Whitney Gym for Faculty"), False),
    ],
)  # fmt: skip
def test_same_event(a, b, expected):
    assert same_event(a, b) is expected


def test_norm_title():
    assert norm_title("The Yale Law & Economics Workshop!") == "law and economics workshop"


@pytest.fixture
def session():
    with make_session_factory("sqlite://")() as s:
        for sid in PRIORITY:
            s.add(Source(id=sid, name=sid, type="x", url="x"))
        yield s


def test_dedupe_picks_canonical_by_source_priority_and_is_recomputed(session):
    session.add_all([
        ev("law-1", "law", "The Fight Over Medication Abortion", description="long description"),
        ev("central-1", "yale-central", "The Fight Over Medication Abortion"),
        ev("eng-1", "engineering", "The Fight Over Medication Abortion", start=T + timedelta(minutes=5)),
        ev("law-2", "law", "Unrelated Talk"),
    ])  # fmt: skip
    session.commit()
    assert dedupe(session, PRIORITY, since=T - timedelta(days=1)) == 2
    dup = {e.id: e.duplicate_of for e in session.scalars(select(Event))}
    assert dup == {"law-1": "central-1", "central-1": None, "eng-1": "central-1", "law-2": None}

    # The central listing is renamed; the next run un-merges it and the other two pair up instead.
    session.get(Event, "central-1").title = "Something Else Entirely"
    session.commit()
    assert dedupe(session, PRIORITY, since=T - timedelta(days=1)) == 1
    dup = {e.id: e.duplicate_of for e in session.scalars(select(Event))}
    assert dup == {"law-1": None, "central-1": None, "eng-1": "law-1", "law-2": None}


def test_dedupe_never_groups_two_events_from_one_source(session):
    # law-a ~ central ~ law-b would chain two distinct law events together.
    session.add_all([
        ev("law-a", "law", "Constitutional Law Colloquium"),
        ev("central", "yale-central", "Constitutional Law Colloquium", start=T + timedelta(minutes=5)),
        ev("law-b", "law", "Constitutional Law Colloquium", start=T + timedelta(minutes=10)),
    ])  # fmt: skip
    session.commit()
    dedupe(session, PRIORITY, since=T - timedelta(days=1))
    dup = {e.id: e.duplicate_of for e in session.scalars(select(Event))}
    assert sum(v is not None for v in dup.values()) == 1
    assert dup["central"] is None
