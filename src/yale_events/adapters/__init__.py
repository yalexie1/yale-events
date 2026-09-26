from yale_events.adapters.base import Adapter, PoliteClient
from yale_events.adapters.engineering import EngineeringAdapter
from yale_events.adapters.ical import ICalAdapter
from yale_events.adapters.localist import LocalistAdapter
from yale_events.adapters.yalesites import YaleSitesAdapter

ADAPTERS: dict[str, type] = {
    "localist": LocalistAdapter,
    "ical": ICalAdapter,
    "engineering": EngineeringAdapter,
    "yalesites": YaleSitesAdapter,
}


def get_adapter(source_type: str, http: PoliteClient) -> Adapter:
    try:
        return ADAPTERS[source_type](http)
    except KeyError:
        raise ValueError(f"no adapter for source type {source_type!r}") from None
