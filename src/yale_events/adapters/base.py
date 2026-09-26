import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from yale_events.config import SourceConfig
from yale_events.schemas import FetchResult


class Adapter(Protocol):
    def fetch(self, source: SourceConfig) -> FetchResult: ...


def make_client() -> httpx.Client:
    # Set YEV_CONTACT (an email or URL) so site admins can reach us about our traffic.
    contact = os.environ.get("YEV_CONTACT")
    ua = "yale-events/0.1" + (f" (+{contact})" if contact else "")
    return httpx.Client(headers={"User-Agent": ua}, timeout=30, follow_redirects=True)


class CacheNamer:
    """Names raw responses `<source>-<stamp>-p<N>.<ext>` in request order, so a replay can serve them back."""

    def __init__(self, source_id: str):
        self.prefix = f"{source_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
        self.n = 0

    def next(self, ext: str) -> str:
        self.n += 1
        return f"{self.prefix}-p{self.n}.{ext}"


class _Helpers:
    def fetch(self, method: str, url: str, *, params=None, data=None, cache_name: str | None = None) -> bytes:
        raise NotImplementedError

    def get_json(self, url: str, params: dict | None = None, cache_name: str | None = None) -> Any:
        return json.loads(self.fetch("GET", url, params=params, cache_name=cache_name))

    def get_text(self, url: str, params: dict | None = None, cache_name: str | None = None) -> str:
        return self.fetch("GET", url, params=params, cache_name=cache_name).decode("utf-8", "replace")

    def post_json(self, url: str, data: dict, cache_name: str | None = None) -> Any:
        return json.loads(self.fetch("POST", url, data=data, cache_name=cache_name))


class PoliteClient(_Helpers):
    """Wraps an httpx client with a minimum delay between requests and optional raw-response caching."""

    def __init__(self, client: httpx.Client, min_interval: float = 1.0, cache_dir: Path | None = None):
        self.client = client
        self.min_interval = min_interval
        self.cache_dir = cache_dir
        self._last = 0.0

    def fetch(self, method: str, url: str, *, params=None, data=None, cache_name: str | None = None) -> bytes:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = self.client.request(method, url, params=params, data=data)
        finally:
            self._last = time.monotonic()
        resp.raise_for_status()
        if self.cache_dir and cache_name:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / cache_name).write_bytes(resp.content)
        return resp.content


class ReplayClient(_Helpers):
    """Serves the most recent cached run instead of the network (for rebuilding after schema/rule changes).

    The adapter's requested cache name (`<source>-<stamp>-p<N>.<ext>`) says which source and which
    request in the run it wants; we substitute the latest cached run for that source.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def fetch(self, method: str, url: str, *, params=None, data=None, cache_name: str | None = None) -> bytes:
        if not cache_name:
            raise ValueError("replay needs a cache_name to locate the response")
        prefix, _, page_part = cache_name.rpartition("-")  # "yale-central-20260926T190400", "p1.json"
        source_id = prefix.rpartition("-")[0]
        first_pages = sorted(self.cache_dir.glob(f"{source_id}-[0-9]*T[0-9]*-p1.*"))
        if not first_pages:
            raise FileNotFoundError(f"no cached responses for {source_id} in {self.cache_dir}")
        stamp = first_pages[-1].name.rpartition("-p1.")[0]
        return (self.cache_dir / f"{stamp}-{page_part}").read_bytes()
