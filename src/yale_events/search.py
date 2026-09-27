"""Text search (`q`): every word must match, and known names match their shorthands.

The query is split into words, and each word must appear in the title, description, venue, or host
groups. A run of words that names something in `data/aliases.yaml` ("yale political union", "YPU")
counts as one word and matches any name in its set, and a building name or alias from
`locations.yaml` ("HQ", "the Whale") also matches events held there. Shorthands (one word of up to 6
characters, like "SOM", "EP&E", or "GH") match only as whole words, so "SOM" doesn't find "some".
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml
from sqlalchemy import ColumnElement, String, and_, cast, or_, true

from yale_events.models import Event
from yale_events.normalize import default_normalizer

DATA_PATH = Path(__file__).parent / "data" / "aliases.yaml"
MAX_SHORTHAND = 6
_PUNCT = "\"'.,;:!?()“”‘’"
# Dropped when the query has other words ("the whale", "talks at hq").
STOPWORDS = frozenset({"a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "with"})


@dataclass(frozen=True)
class Term:
    names: frozenset[str]  # lowercase; any one may match
    buildings: frozenset[str] = frozenset()


def is_shorthand(name: str) -> bool:
    return " " not in name and len(name) <= MAX_SHORTHAND


def _words(s: str) -> list[str]:
    return [w for w in (w.strip(_PUNCT) for w in s.lower().split()) if w]


@cache
def load_aliases(path: Path = DATA_PATH) -> dict[str, frozenset[str]]:
    """Name as query words ("ethics politics and economics") -> every lowercase name in its sets."""
    index: dict[str, set[str]] = {}
    for names in yaml.safe_load(path.read_text())["aliases"]:
        group = {n.lower() for n in names}
        for n in group:
            index.setdefault(" ".join(_words(n)), set()).update(group)
    return {n: frozenset(g) for n, g in index.items()}


@cache
def building_names() -> dict[str, str]:
    return {" ".join(_words(n)): b for n, b in default_normalizer().locations.search_names.items()}


def parse_query(q: str, aliases: dict[str, frozenset[str]], buildings: dict[str, str]) -> list[Term]:
    """Split a query into terms, joining the longest runs of words that are a known name."""
    tokens = _words(q)
    longest = max((len(n.split()) for n in [*aliases, *buildings]), default=1)
    terms, i = [], 0
    while i < len(tokens):
        for size in range(min(longest, len(tokens) - i), 0, -1):
            phrase = " ".join(tokens[i : i + size])
            if phrase in aliases or phrase in buildings or size == 1:
                names = aliases.get(phrase, frozenset({phrase}))
                ids = frozenset(b for n in {phrase, *names} if (b := buildings.get(" ".join(_words(n)))))
                terms.append(Term(names, ids))
                i += size
                break
    content = [t for t in terms if not t.names <= STOPWORDS]
    return content or terms


def _name_clause(name: str, known: bool) -> ColumnElement[bool]:
    fields = [Event.title, Event.description, Event.location_name, cast(Event.groups, String)]
    if known and is_shorthand(name):
        pattern = rf"(?i)(?<![\w&]){re.escape(name)}(?![\w&])"
        return or_(*(f.regexp_match(pattern) for f in fields))
    # SQLite's LIKE is case-insensitive for ASCII; escape the wildcards a user might type.
    like = "%" + name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return or_(*(f.ilike(like, escape="\\") for f in fields))


def search_clause(q: str) -> ColumnElement[bool]:
    aliases, buildings = load_aliases(), building_names()
    known = frozenset().union(*aliases.values(), buildings)
    clauses = []
    for t in parse_query(q, aliases, buildings):
        matches = [_name_clause(n, n in known) for n in sorted(t.names)]
        if t.buildings:
            matches.append(Event.location_id.in_(t.buildings))
        clauses.append(or_(*matches))
    return and_(*clauses) if clauses else true()
