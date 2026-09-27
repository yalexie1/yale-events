from yale_events.search import load_aliases, parse_query

BUILDINGS = {"hq": "humanities-quadrangle", "whale": "ingalls-rink"}


def parse(q: str) -> list[tuple[list[str], list[str]]]:
    return [(sorted(t.names), sorted(t.buildings)) for t in parse_query(q, load_aliases(), BUILDINGS)]


def test_shorthand_and_full_name_are_one_term():
    both = ["yale political union", "ypu"]
    assert parse("YPU") == [(both, [])]
    assert parse("Yale Political Union debate") == [(both, []), (["debate"], [])]


def test_punctuation_in_names_is_ignored():
    assert parse("ethics politics and economics")[0][0] == parse("EP&E")[0][0]


def test_building_aliases_and_stopwords():
    assert parse("the Whale") == [(["whale"], ["ingalls-rink"])]
    assert parse("talks at HQ") == [(["talks"], []), (["hq"], ["humanities-quadrangle"])]
    assert parse("the") == [(["the"], [])]


def test_aliases_file_is_consistent():
    aliases = load_aliases()
    # Every shorthand resolves to at least one other name.
    assert all(len(names) > 1 for names in aliases.values())
    # YSM is Medicine's; Music's full name must not pull in Medicine's events.
    assert "yale school of music" not in aliases
