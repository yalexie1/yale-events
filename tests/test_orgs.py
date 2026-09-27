import pytest

from yale_events.config import load_sources
from yale_events.normalize import default_normalizer
from yale_events.orgs import default_orgs, load_orgs, sort_key


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


def test_org_group_contains():
    orgs = default_orgs()
    assert orgs["student-orgs"].matches("yale-connect", ["Migration Alliance at Yale - An Undergraduate Organization"], None)
    assert orgs["student-orgs"].matches("yale-connect", ["The Yale Political Union (Undergraduate)"], None)
    assert not orgs["student-orgs"].matches("yale-connect", ["Office of Sustainability"], None)


def test_orgs_sort_by_kind_then_name():
    ordered = [o for o in sorted(default_orgs().values(), key=sort_key) if o.kind == "school"]
    names = [o.name for o in ordered]
    assert names[:2] == ["School of Architecture", "School of Art"]
    assert names.index("Law School") < names.index("School of Management") < names.index("School of Music")


def test_org_name_with_unquoted_comma_is_rejected(tmp_path):
    path = tmp_path / "orgs.yaml"
    path.write_text("colleges: []\nschools: []\norganizations: []\n"
                    "departments:\n  - {id: wgss, name: Women's, Gender & Sexuality Studies}\n")
    with pytest.raises(ValueError, match="unknown keys"):
        load_orgs(path)
