from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_SOURCES_PATH = Path("sources.yaml")


class SourceConfig(BaseModel):
    id: str
    name: str
    type: str
    url: str
    enabled: bool = True
    # Fallbacks when an event carries no usable category/location of its own.
    default_category: str | None = None
    default_location: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


def load_sources(path: Path = DEFAULT_SOURCES_PATH) -> list[SourceConfig]:
    data = yaml.safe_load(path.read_text())
    return [SourceConfig(**s) for s in data["sources"]]
