from yale_events.adapters.base import Adapter, PoliteClient
from yale_events.adapters.drupal_calendar import DrupalCalendarAdapter
from yale_events.adapters.engineering import EngineeringAdapter
from yale_events.adapters.ical import ICalAdapter
from yale_events.adapters.jsonld import JsonLdAdapter
from yale_events.adapters.localist import LocalistAdapter
from yale_events.adapters.macmillan import MacMillanAdapter
from yale_events.adapters.music import MusicAdapter
from yale_events.adapters.peabody import PeabodyAdapter
from yale_events.adapters.tsai import TsaiCityAdapter
from yale_events.adapters.yalesites import YaleSitesAdapter
from yale_events.adapters.ycba import YCBAAdapter
from yale_events.adapters.ysm import YSMAdapter

ADAPTERS: dict[str, type] = {
    "localist": LocalistAdapter,
    "ical": ICalAdapter,
    "engineering": EngineeringAdapter,
    "yalesites": YaleSitesAdapter,
    "ysm": YSMAdapter,
    "yale-music": MusicAdapter,
    "drupal-calendar": DrupalCalendarAdapter,
    "jsonld": JsonLdAdapter,
    "peabody": PeabodyAdapter,
    "macmillan": MacMillanAdapter,
    "ycba": YCBAAdapter,
    "tsai-city": TsaiCityAdapter,
}


def get_adapter(source_type: str, http: PoliteClient) -> Adapter:
    try:
        return ADAPTERS[source_type](http)
    except KeyError:
        raise ValueError(f"no adapter for source type {source_type!r}") from None
