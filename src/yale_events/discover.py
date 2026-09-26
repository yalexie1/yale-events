"""Find machine-readable event feeds on a site, to decide which adapter a new source needs.

Looks for (in the page and up to MAX_FOLLOW linked calendar pages on the same host):
iCal links, RSS/Atom alternates, Google Calendar embeds, Localist (events.yale.edu) widgets,
and WordPress "The Events Calendar" sites. Respects robots.txt for every page it fetches.
"""

import base64
import re
import time
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import parse_qs, quote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

MAX_FOLLOW = 2
MIN_INTERVAL = 1.0

_HREF = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_EVENTS_PATH = re.compile(r"/(events?|calendar|happenings)(/|$|\?)", re.IGNORECASE)
_LOCALIST = re.compile(r"events\.yale\.edu/(?:widget|api/2)[^\"'\s]*", re.IGNORECASE)


@dataclass
class Feed:
    kind: str  # ical | rss | google-calendar | localist | tribe-events
    url: str
    found_on: str


@dataclass
class Report:
    url: str
    final_url: str | None = None
    status: int | None = None
    blocked_by_robots: list[str] = field(default_factory=list)
    feeds: list[Feed] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    error: str | None = None


def google_ics(embed_url: str) -> list[str]:
    """calendar.google.com/calendar/embed?src=ID -> the public iCal URL for each calendar."""
    srcs = parse_qs(urlparse(unescape(embed_url)).query).get("src", [])
    return [f"https://calendar.google.com/calendar/ical/{quote(_calendar_id(s), safe='')}/public/basic.ics" for s in srcs]


def _calendar_id(src: str) -> str:
    """Newer embeds base64-encode the calendar id ("NDE0...Y29t" -> "414a...@group.calendar.google.com")."""
    if "@" in src:
        return src
    try:
        decoded = base64.b64decode(src + "=" * (-len(src) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return src
    return decoded if "@" in decoded else src


def find_feeds(html: str, page_url: str) -> tuple[list[Feed], list[str]]:
    """Feeds on this page, and same-host links that look like event listings (to follow next)."""
    feeds: list[Feed] = []
    for tag in _LINK_TAG.findall(html):
        if re.search(r"application/(rss|atom)\+xml", tag, re.I) and (m := _HREF.search(tag)):
            feeds.append(Feed("rss", urljoin(page_url, unescape(m.group(1))), page_url))
        elif "text/calendar" in tag.lower() and (m := _HREF.search(tag)):
            feeds.append(Feed("ical", urljoin(page_url, unescape(m.group(1))), page_url))

    host = urlparse(page_url).netloc
    follow: list[str] = []
    for raw in _HREF.findall(html):
        href = unescape(raw)
        url = urljoin(page_url, href.replace("webcal://", "https://"))
        low = href.lower()
        if "calendar.google.com/calendar" in low and "src=" in low:
            feeds += [Feed("google-calendar", u, page_url) for u in google_ics(url)]
        elif low.startswith("webcal:") or re.search(r"\.ics($|\?)|[?&](ical|outlook-ical)=1|/ical(/|$)", low):
            feeds.append(Feed("ical", url, page_url))
        elif urlparse(url).netloc == host and _EVENTS_PATH.search(urlparse(url).path + "/"):
            follow.append(url.split("#")[0])
    feeds += [Feed("localist", "https://" + m, page_url) for m in _LOCALIST.findall(html)]
    if "tribe-events" in html:
        base = f"{urlparse(page_url).scheme}://{host}"
        feeds.append(Feed("tribe-events", f"{base}/wp-json/tribe/events/v1/events", page_url))

    seen: set[str] = set()
    unique = [f for f in feeds if not (f.url in seen or seen.add(f.url))]
    return unique, list(dict.fromkeys(follow))


class Discoverer:
    def __init__(self, client: httpx.Client, min_interval: float = MIN_INTERVAL):
        self.client = client
        self.min_interval = min_interval
        self._robots: dict[str, RobotFileParser] = {}
        self._last: dict[str, float] = {}

    def _allowed(self, url: str) -> bool:
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self._robots:
            rp = RobotFileParser()
            try:
                resp = self._get(f"{root}/robots.txt")
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except httpx.HTTPError:
                rp.parse([])
            self._robots[root] = rp
        return self._robots[root].can_fetch(self.client.headers["User-Agent"], url)

    def _get(self, url: str) -> httpx.Response:
        host = urlparse(url).netloc
        if (wait := self._last.get(host, 0) + self.min_interval - time.monotonic()) > 0:
            time.sleep(wait)
        try:
            return self.client.get(url)
        finally:
            self._last[host] = time.monotonic()

    def discover(self, url: str) -> Report:
        report = Report(url)
        queue, visited = [url], set()
        while queue and len(visited) <= MAX_FOLLOW:
            page = queue.pop(0)
            if page in visited:
                continue
            visited.add(page)
            if not self._allowed(page):
                report.blocked_by_robots.append(page)
                continue
            try:
                resp = self._get(page)
            except httpx.HTTPError as e:
                report.error = f"{type(e).__name__}: {e}"
                continue
            if page == url:
                report.final_url, report.status = str(resp.url), resp.status_code
            report.pages.append(f"{resp.status_code} {resp.url}")
            if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
                continue
            feeds, follow = find_feeds(resp.text, str(resp.url))
            report.feeds += [f for f in feeds if f.url not in {x.url for x in report.feeds}]
            queue += [u for u in follow if u not in visited]
        return report
