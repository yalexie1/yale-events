from datetime import date, datetime, timedelta

import pytest

from yale_events.config import SourceConfig
from yale_events.normalize import Normalizer
from yale_events.normalize.location import norm_address, norm_name
from yale_events.normalize.time import NEW_HAVEN, clean_times, is_ongoing
from yale_events.schemas import RawEvent

N = Normalizer()
LOC = N.locations
CAT = N.categorizer


# --- locations -------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name, address, lat, lon, virtual, expected",
    [
        # Aliases and spelling variants seen in real Localist data.
        ("Schwarzman Center", None, None, None, False, ("schwarzman-center", "central", None)),
        ("Yale Schwarzman Center", None, None, None, False, ("schwarzman-center", "central", None)),
        ("Woolsey hall", None, None, None, False, ("woolsey-hall", "central", None)),
        ("Beinecke Rare Book and Manuscript Library", None, None, None, False, ("beinecke", "central", None)),
        ("Henry R. Luce Hall", None, None, None, False, ("luce-hall", "science-hill", None)),
        ("Morse Recital Hall", None, None, None, False, ("sprague-hall", "central", "Morse Recital Hall")),
        # Room codes.
        ("LC 101", "63 High Street", 52.4857, -2.1329, False, ("linsly-chittenden", "central", "101")),
        ("HQ 136", "320 York Street", 45.9533, -66.6515, False, ("humanities-quadrangle", "central", "136")),
        # Room before building; building identified by address segment.
        ("Burke Auditorium, Kroon Hall", None, None, None, False, ("kroon-hall", "science-hill", "Burke Auditorium")),
        ("Iseman Theater, 1156 Chapel Street", None, None, None, False, ("green-hall", "arts-district", "Iseman Theater")),
        ("Room 101, Kroon Hall", None, None, None, False, ("kroon-hall", "science-hill", "Room 101")),
        (
            "Yale School of Management, Zhang Auditorium, 165 Whitney Ave, 06511, CT New Haven, United States",
            None, None, None, False, ("evans-hall", "science-hill", None),
        ),
        # Unknown name, but the address matches (geocode is wrong: Sydney).
        ("Some Seminar Room", "320 York St", -33.868, 151.2059, False, ("humanities-quadrangle", "central", None)),
        # Virtual wins even when a host venue is named.
        ("Schwarzman Center", None, None, None, True, (None, "online", None)),
        ("Online", None, None, None, False, (None, "online", None)),
        ("TBD", None, None, None, False, (None, None, None)),
        # Real off-campus venue with a full address.
        ("Alice Tully Hall", "1941 Broadway at W 65th St, New York, NY 10023", 40.7728, -73.9823, False,
         (None, "off-campus", None)),
        # Bad geocode, no city in the address: unknown rather than off-campus.
        ("Mystery Studio", "12 Nowhere Street", 40.7007, -73.9874, False, (None, None, None)),
        # Unlisted venue right next to a known building takes that building's area.
        ("Some Lab Annex", None, 41.3175, -72.9230, False, (None, "science-hill", None)),
        ("New Haven Museum", None, 41.314, -72.922, False, ("new-haven-museum", "off-campus", None)),
        # Athletics venues: the city segments are not the room.
        ("New Haven, Conn. , Ingalls Rink", None, None, None, False, ("ingalls-rink", "science-hill", None)),
        ("New Haven, Conn., John J. Lee Amphitheater", None, None, None, False,
         ("payne-whitney-gym", "arts-district", "John J. Lee Amphitheater")),
        # "Room N" after a room code or a building name.
        ("SLB Room 127", None, None, None, False, ("law-school", "central", "127")),
        ("Humanities Quadrangle Room 107", None, None, None, False, ("humanities-quadrangle", "central", "Room 107")),
        ("Kroon Hall, Rm. 321", None, None, None, False, ("kroon-hall", "science-hill", "Room 321")),
        # A parenthetical address (Yale Connect): its comma mustn't split the building name, and it
        # can identify the building when the prose around it doesn't.
        ("Farnam Memorial Gardens (335 Prospect Street, New Haven), New Haven, CT 06520, United States",
         None, None, None, False, ("farnam-gardens", "science-hill", None)),
        ("Room HQ L01 in the Humanities Quadrangle (320 York Street, New Haven)., New Haven, CT 06520, United States",
         None, None, None, False, ("humanities-quadrangle", "central", None)),
        ("Harkness Hall (Medical)", None, None, None, False, ("es-harkness-hall", "medical", None)),
        ("Room 101, Harkness Hall (Medical)", None, None, None, False, ("es-harkness-hall", "medical", "Room 101")),
        # A street address run into the venue name (MacMillan).
        ("Room 203, Luce Hall 34 Hillhouse Avenue, New Haven, CT06511", None, None, None, False,
         ("luce-hall", "science-hill", "Room 203")),
        ("Luce 101 34 Hillhouse Avenue, New Haven, CT06511", None, None, None, False, ("luce-hall", "science-hill", "101")),
        ("Seminar Room 34 Hillhouse Ave.", None, None, None, False, ("luce-hall", "science-hill", None)),
        ("ISPS, Room A002", None, None, None, False, ("isps", "science-hill", "Room A002")),
        # Yale Connect venues: a street address first, or a room code followed by a room name.
        ("55 Whitney Ave, 3rd Floor, Room 369 , 55 Whitney Ave, New Haven, CT 06510, United States",
         None, None, None, False, ("55-whitney-avenue", "central", None)),
        ("Hope 103 Amphitheater, New Haven, CT 06520, United States", None, None, None, False,
         ("hope-building", "medical", "Hope 103 Amphitheater")),
    ],
)  # fmt: skip
def test_resolve_location(name, address, lat, lon, virtual, expected):
    m = LOC.resolve(name, address, lat, lon, virtual)
    assert (m.location_id, m.area, m.room) == expected


def test_norm_helpers():
    assert norm_name("The Yale  Art Gallery") == "art gallery"
    assert norm_name("Phelps Hall & Gate") == "phelps hall and gate"
    assert norm_address("320 York Street, New Haven, CT 06511") == "320 york st"
    assert norm_address("168 Grove Street New Haven, CT, 06511") == "168 grove st new haven"
    assert norm_address("Niebuhr Hall, Room N123") is None


# --- categories ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "title, description, tags, expected",
    [
        ("Anything", None, ["Talks & Lectures", "Social Sciences"], ["talks"]),
        ("Anything", None, ["Music", "Performances"], ["arts-performance", "music"]),
        # Broad-only tags fall through to keywords.
        ("YQI Colloquium - Kenneth Brown", None, ["Science & Technology"], ["talks"]),
        ("Scott Hartman, trombone", "Faculty Artist Series", [], ["music"]),
        ("Computational Research Support OHs", None, [], ["academic"]),
        ("Call for Proposals: CCAM Studio Fellowship", None, [], ["career"]),
        ("West of the River", "In West of the River, photographer Christian Badach describes...", [], ["exhibitions"]),
        ("Marital Privilege", "Serena Mayeri recounts the work of the activists who challenged...", [], ["talks"]),
        ("The Story of My Life", "The Story of My Life follows the friendship of Alvin and Thomas...", [], ["arts-performance"]),
        # Keyword false-positive guards.
        ("Wei-Yi Yang, Bach, and Schubert", "Wei-Yi Yang traces elements of stylized dance through...", [], ["arts-performance"]),
        ("Satire, Sympathy, and Social Critique", None, [], []),
        ("Mass Incarceration in America", None, [], []),
        ("Game Theory and Elections", None, [], []),
    ],
)  # fmt: skip
def test_categorize(title, description, tags, expected):
    assert CAT.categorize(title, description, tags) == expected


def test_categorize_default_only_when_nothing_matched():
    assert CAT.categorize("Untitled", None, [], default="cultural") == ["cultural"]
    assert CAT.categorize("Jazz night", None, [], default="cultural") == ["music"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Free and open to all. Lunch will be provided.", True),
        ("Drinks and light snacks will be offered during the talk", True),
        ("A casual reception (salad, pizza) follows the class.", True),
        ("Refreshments will be served.", True),
        ("A lecture on the history of food systems.", False),
        ("Registration required.", False),
    ],
)
def test_free_food(text, expected):
    assert CAT.free_food("Some Event", text) is expected


# --- time ------------------------------------------------------------------------------------

def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def test_clean_times_naive_assumed_local():
    start, end = clean_times(datetime(2026, 11, 1, 14), datetime(2026, 11, 1, 15), False)
    assert start.tzinfo == NEW_HAVEN and end - start == timedelta(hours=1)


def test_clean_times_across_dst_change():
    # 1:30 AM on Nov 1, 2026 is ambiguous; zoneinfo picks the first (EDT). Duration must stay real.
    start, end = clean_times(dt("2026-11-01T00:30:00-04:00"), dt("2026-11-01T03:30:00-05:00"), False)
    assert end - start == timedelta(hours=4)


def test_clean_times_all_day_drops_end_and_pins_local_midnight():
    start, end = clean_times(dt("2026-09-26T04:00:00+00:00"), dt("2026-09-27T04:00:00+00:00"), True)
    assert start.astimezone(NEW_HAVEN).hour == 0 and end is None


@pytest.mark.parametrize("end", ["2026-09-26T09:00:00-04:00", "2026-09-26T10:00:00-04:00", "2026-12-26T10:00:00-05:00"])
def test_clean_times_drops_bogus_end(end):
    _, cleaned = clean_times(dt("2026-09-26T10:00:00-04:00"), dt(end), False)
    assert cleaned is None


@pytest.mark.parametrize(
    "first, last, all_day, cats, expected",
    [
        (date(2026, 6, 8), date(2026, 11, 8), True, [], True),  # months-long all-day series
        (date(2026, 9, 1), date(2026, 12, 1), False, ["exhibitions"], True),  # gallery open hours
        (date(2026, 9, 1), date(2026, 12, 1), False, ["talks"], False),  # weekly seminar series
        (date(2026, 9, 26), date(2026, 9, 28), True, [], False),  # weekend festival
        (None, None, True, [], False),
    ],
)
def test_is_ongoing(first, last, all_day, cats, expected):
    assert is_ongoing(first, last, all_day, cats) is expected


# --- end to end ------------------------------------------------------------------------------

def test_normalize_uses_source_defaults():
    src = SourceConfig(
        id="jec", name="JE", type="ical", url="https://x", default_category="social",
        default_location="Jonathan Edwards College",
    )  # fmt: skip
    raw = RawEvent(source_event_id="1", title="Untitled", start=dt("2026-10-01T18:00:00-04:00"))
    v = N.normalize(raw, src)
    assert v["categories"] == ["social"]
    assert (v["location_id"], v["area"]) == ("jonathan-edwards-college", "central")
    assert "source_event_id" not in v


@pytest.mark.parametrize(
    "name, address, expected",
    [
        ("Hope Memorial Building", "315 Cedar Street", ("hope-building", None)),
        ("Sterling Hall of Medicine, L-Wing", "333 Cedar Street", ("sterling-hall-of-medicine", None)),
        ("M.S. Harkness Memorial Auditorium", "333 Cedar Street", ("sterling-hall-of-medicine", "M.S. Harkness Memorial Auditorium")),
        ("Yale School of Public Health (LEPH)", "60 College Street", ("ysph", None)),
        ("Farnam Memorial Building", "310 Cedar Street", ("farnam-building", None)),
        ("35 Park St", "35 Park St", ("smilow", None)),
    ],
)  # fmt: skip
def test_medical_campus_locations(name, address, expected):
    m = LOC.resolve(name, address)
    assert (m.location_id, m.room) == expected
    assert m.area == "medical"
