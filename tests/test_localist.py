import json
from datetime import UTC
from pathlib import Path

import httpx
import respx

from yale_events.adapters.base import PoliteClient
from yale_events.adapters.localist import LocalistAdapter, parse_event
from yale_events.config import SourceConfig

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "localist_events.json").read_text())
SOURCE = SourceConfig(id="yale-central", name="Yale", type="localist", url="https://events.yale.edu", options={"days": 7})


def test_parse_event_maps_fields():
    ev = parse_event(FIXTURE["events"][0]["event"])
    assert ev.title == "An Artist’s America: Toward a More Perfect Union"
    assert ev.source_event_id == "52833940153252"
    assert ev.all_day is True
    assert ev.start.utcoffset() is not None
    assert ev.location_name == "Haas Family Arts Library"
    assert ev.room == "Lower Level"
    assert ev.lat == 41.308754
    assert ev.groups == ["Yale Arts", "Yale Library"]
    assert "General Public" in ev.audience
    assert ev.url.startswith("https://events.yale.edu/event/")
    assert ev.cancelled is False


def test_parse_event_virtual_and_free():
    ev = parse_event(FIXTURE["events"][1]["event"])
    assert ev.virtual is True
    assert ev.free is True
    assert ev.cost == "Free and open to the public"


@respx.mock
def test_fetch_follows_pagination():
    page1 = {**FIXTURE, "events": FIXTURE["events"][:2], "page": {"current": 1, "next_page": 2}}
    page2 = {**FIXTURE, "events": FIXTURE["events"][2:], "page": {"current": 2, "next_page": None}}
    route = respx.get("https://events.yale.edu/api/2/events").mock(
        side_effect=lambda req: httpx.Response(200, json=page1 if req.url.params["page"] == "1" else page2)
    )
    with httpx.Client() as client:
        result = LocalistAdapter(PoliteClient(client, min_interval=0)).fetch(SOURCE)

    assert route.call_count == 2
    assert route.calls[0].request.url.params["days"] == "7"
    assert len(result.events) == 3
    assert (result.window_end - result.window_start).days == 7
    assert result.window_start.astimezone(UTC).hour in (4, 5)  # local midnight in New Haven
