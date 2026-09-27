"""Organizations behind events (colleges, departments, offices), matched by source, group, or building."""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

DATA_PATH = Path(__file__).parent / "data" / "organizations.yaml"
KINDS = {"colleges": "college", "departments": "department", "organizations": "organization"}


@dataclass(frozen=True)
class Org:
    id: str
    name: str
    kind: str  # college | department | organization
    sources: frozenset[str] = frozenset()
    groups: frozenset[str] = frozenset()
    locations: frozenset[str] = frozenset()

    def matches(self, source_id: str, groups: list[str] | None, location_id: str | None) -> bool:
        return source_id in self.sources or location_id in self.locations or not self.groups.isdisjoint(groups or ())


def load_orgs(path: Path = DATA_PATH) -> dict[str, Org]:
    data = yaml.safe_load(path.read_text())
    orgs: dict[str, Org] = {}
    for section, kind in KINDS.items():
        for o in data[section]:
            if o["id"] in orgs:
                raise ValueError(f"duplicate organization id {o['id']!r}")
            orgs[o["id"]] = Org(
                o["id"], o["name"], kind,
                frozenset(o.get("sources", [])), frozenset(o.get("groups", [])), frozenset(o.get("locations", [])),
            )  # fmt: skip
    return orgs


@cache
def default_orgs() -> dict[str, Org]:
    return load_orgs()
