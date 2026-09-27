"""Organizations behind events (colleges, departments, offices), matched by source, group, or building."""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

DATA_PATH = Path(__file__).parent / "data" / "organizations.yaml"
KINDS = {"colleges": "college", "schools": "school", "departments": "department", "organizations": "organization"}
FIELDS = {"id", "name", "sources", "groups", "group_contains", "locations"}


@dataclass(frozen=True)
class Org:
    id: str
    name: str
    kind: str  # college | school | department | organization
    sources: frozenset[str] = frozenset()
    groups: frozenset[str] = frozenset()
    locations: frozenset[str] = frozenset()
    group_contains: tuple[str, ...] = ()  # lowercase substrings of group names ("an undergraduate organization")

    def has_group(self, group: str) -> bool:
        return group in self.groups or any(sub in group.lower() for sub in self.group_contains)

    def matches(self, source_id: str, groups: list[str] | None, location_id: str | None) -> bool:
        return source_id in self.sources or location_id in self.locations or any(map(self.has_group, groups or ()))


def load_orgs(path: Path = DATA_PATH) -> dict[str, Org]:
    data = yaml.safe_load(path.read_text())
    orgs: dict[str, Org] = {}
    for section, kind in KINDS.items():
        for o in data[section]:
            if extra := set(o) - FIELDS:
                # Usually an unquoted comma in a {...} name: "{name: Women's, Gender & ...}".
                raise ValueError(f"organization {o['id']!r}: unknown keys {sorted(extra)}")
            if o["id"] in orgs:
                raise ValueError(f"duplicate organization id {o['id']!r}")
            orgs[o["id"]] = Org(
                o["id"], o["name"], kind,
                frozenset(o.get("sources", [])), frozenset(o.get("groups", [])), frozenset(o.get("locations", [])),
                tuple(sub.lower() for sub in o.get("group_contains", [])),
            )  # fmt: skip
    return orgs


def sort_key(org: Org) -> tuple[int, str]:
    """Kind (colleges, schools, departments, others), then name ignoring "Yale", "The", and "School of":
    Architecture, Art, Divinity, ..., Music; Athletics, Library, ..., Shops at Yale."""
    name = re.sub(r"^((yale|the|university|school of( the)?)\s+)+", "", org.name.lower())
    return list(KINDS.values()).index(org.kind), name


@cache
def default_orgs() -> dict[str, Org]:
    return load_orgs()
