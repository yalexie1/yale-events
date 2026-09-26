import json
import os
import time
from pathlib import Path
from typing import Protocol

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


class PoliteClient:
    """Wraps an httpx client with a minimum delay between requests and optional raw-response caching."""

    def __init__(self, client: httpx.Client, min_interval: float = 1.0, cache_dir: Path | None = None):
        self.client = client
        self.min_interval = min_interval
        self.cache_dir = cache_dir
        self._last = 0.0

    def get_json(self, url: str, params: dict, cache_name: str | None = None) -> dict:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        resp = self.client.get(url, params=params)
        resp.raise_for_status()
        if self.cache_dir and cache_name:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / cache_name).write_bytes(resp.content)
        return resp.json()


class ReplayClient:
    """Serves the most recent cached run instead of the network (for rebuilding after schema/rule changes).

    Cache files are named `<source>-<timestamp>-p<page>.json`; the adapter's requested name tells us
    which source and page it wants, and we substitute the latest cached run for that source.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def get_json(self, url: str, params: dict, cache_name: str | None = None) -> dict:
        if not cache_name:
            raise ValueError("replay needs a cache_name to locate the response")
        prefix, _, page_part = cache_name.rpartition("-")  # "yale-central-20260926T190400", "p1.json"
        source_id = prefix.rpartition("-")[0]
        first_pages = sorted(self.cache_dir.glob(f"{source_id}-[0-9]*T[0-9]*-p1.json"))
        if not first_pages:
            raise FileNotFoundError(f"no cached responses for {source_id} in {self.cache_dir}")
        stamp = first_pages[-1].name.removesuffix("-p1.json")
        return json.loads((self.cache_dir / f"{stamp}-{page_part}").read_text())
