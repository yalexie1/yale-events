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
