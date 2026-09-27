import httpx
import pytest
import respx

from yale_events.adapters.base import PoliteClient

URL = "https://example.edu/feed"


def client(tmp_path=None):
    return PoliteClient(httpx.Client(), min_interval=0, cache_dir=tmp_path, retry_delays=(0, 0))


@respx.mock
def test_retries_transient_errors(tmp_path):
    route = respx.get(URL).mock(side_effect=[httpx.ReadTimeout("slow"), httpx.Response(503), httpx.Response(200, text="ok")])
    assert client(tmp_path).fetch("GET", URL, cache_name="src-20260101T000000-p1.txt") == b"ok"
    assert route.call_count == 3
    assert [p.name for p in tmp_path.iterdir()] == ["src-20260101T000000-p1.txt"]


@respx.mock
def test_gives_up_after_retries():
    route = respx.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        client().fetch("GET", URL)
    assert route.call_count == 3


@respx.mock
def test_does_not_retry_client_errors():
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        client().fetch("GET", URL)
    assert route.call_count == 1
