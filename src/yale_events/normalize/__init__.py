from functools import cache
from pathlib import Path
from typing import Any

from yale_events.config import SourceConfig
from yale_events.normalize.category import Categorizer
from yale_events.normalize.location import LocationResolver
from yale_events.normalize.time import clean_times, is_ongoing
from yale_events.schemas import RawEvent

DATA_DIR = Path(__file__).parent.parent / "data"


class Normalizer:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.locations = LocationResolver(data_dir / "locations.yaml")
        self.categorizer = Categorizer(data_dir / "categories.yaml")

    def normalize(self, raw: RawEvent, source: SourceConfig) -> dict[str, Any]:
        """Event column values for this occurrence: the raw fields, cleaned, plus derived ones."""
        values = raw.model_dump()
        values["start"], values["end"] = clean_times(raw.start, raw.end, raw.all_day)

        loc = self.locations.resolve(raw.location_name, raw.address, raw.lat, raw.lon, raw.virtual)
        if loc.location_id is None and loc.area is None and source.default_location:
            loc = self.locations.resolve(source.default_location)
        values["location_id"], values["area"] = loc.location_id, loc.area
        values["room"] = raw.room or loc.room

        categories = self.categorizer.categorize(
            raw.title, raw.description, raw.tags + source.tags, source.default_category
        )
        values["categories"] = categories
        values["free_food"] = self.categorizer.free_food(raw.title, raw.description)
        values["ongoing"] = is_ongoing(raw.series_first_date, raw.series_last_date, raw.all_day, categories)
        del values["source_event_id"]
        return values


@cache
def default_normalizer() -> Normalizer:
    return Normalizer()
