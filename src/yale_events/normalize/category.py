import re
from pathlib import Path

import yaml

DESCRIPTION_PREFIX = 300


class Categorizer:
    def __init__(self, path: Path):
        data = yaml.safe_load(path.read_text())
        self.categories: dict[str, str] = data["categories"]
        self.tag_map: dict[str, list[str]] = {k.lower(): v for k, v in data["tag_map"].items()}
        self.keywords = {c: re.compile(p, re.IGNORECASE) for c, p in data["keywords"].items()}
        self.free_food_re = re.compile(data["free_food"], re.IGNORECASE)
        for cats in [*self.tag_map.values(), [*self.keywords]]:
            if unknown := set(cats) - set(self.categories):
                raise ValueError(f"unknown categories in rules: {unknown}")

    def categorize(self, title: str, description: str | None, tags: list[str], default: str | None = None) -> list[str]:
        found = {c for t in tags for c in self.tag_map.get(t.lower(), [])}
        if not found:
            found = self._keywords(title)
        if not found and description:
            found = self._keywords(description[:DESCRIPTION_PREFIX])
        if not found and default:
            found = {default}
        return sorted(found)

    def _keywords(self, text: str) -> set[str]:
        return {c for c, rx in self.keywords.items() if rx.search(text)}

    def free_food(self, title: str, description: str | None) -> bool:
        return bool(self.free_food_re.search(f"{title}\n{description or ''}"))
