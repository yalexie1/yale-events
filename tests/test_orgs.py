from yale_events.config import load_sources
from yale_events.normalize import default_normalizer
from yale_events.orgs import default_orgs


def test_orgs_reference_known_sources_and_buildings():
    sources = {s.id for s in load_sources()}
    buildings = default_normalizer().locations.buildings
    orgs = default_orgs().values()
    assert {s for o in orgs for s in o.sources} <= sources
    assert {b for o in orgs for b in o.locations} <= set(buildings)
    assert sum(o.kind == "college" for o in orgs) == 14


def test_org_matches_source_group_or_building():
    orgs = default_orgs()
    assert orgs["pauli-murray"].matches("pauli-murray", [], None)
    assert orgs["pauli-murray"].matches("yale-central", [], "pauli-murray-college")
    assert orgs["computer-science"].matches("yale-central", ["Computer Science"], None)
    assert orgs["engineering"].matches("yale-central", ["Computer Science"], None)
    assert not orgs["law"].matches("yale-central", ["Yale Law Democrats"], None)
