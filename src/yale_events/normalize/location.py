import math
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Rough box around New Haven + West Haven; Localist geocodes outside it are often wrong
# ("320 York St" resolves to Canada or Sydney), so we only trust coordinates inside it.
YALE_BBOX = (41.24, 41.34, -73.01, -72.89)  # lat_min, lat_max, lon_min, lon_max
NEAREST_MAX_METERS = 150
UNKNOWN_NAMES = {"tbd", "tba", "other", "none", "see description", "various locations"}
ONLINE_NAMES = {"online", "virtual", "zoom", "webinar"}
# Segments that name the city, not a room: "New Haven, Conn., Ingalls Rink".
_CITY_SEGMENT = re.compile(r"^(new haven|conn\.?|ct|connecticut)$", re.IGNORECASE)
_TRAILING_ROOM = re.compile(r"^(.+?)[\s,]+(?:room|rm\.?)\s*([A-Z]?\d+[A-Z]?)$", re.IGNORECASE)

_STREET_ABBR = {
    "street": "st", "avenue": "ave", "road": "rd", "drive": "dr", "place": "pl", "boulevard": "blvd", "lane": "ln",
}  # fmt: skip


@dataclass(frozen=True)
class Building:
    id: str
    name: str
    area: str
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True)
class LocationMatch:
    location_id: str | None
    area: str | None
    room: str | None = None


def norm_name(s: str) -> str:
    s = s.lower().replace("&", " and ").replace("’", "'")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    s = re.sub(r"^((the|yale) )+", "", " ".join(s.split()))
    return s


def norm_address(s: str) -> str | None:
    """'320 York Street, New Haven, CT' -> '320 york st'. Returns None if it doesn't look like a street address."""
    first = s.split(",")[0].lower().replace(".", "")
    words = [_STREET_ABBR.get(w, w) for w in re.sub(r"[^a-z0-9 ]+", " ", first).split()]
    if len(words) < 2 or not words[0].isdigit():
        return None
    return " ".join(words)


def _meters(lat1, lon1, lat2, lon2) -> float:
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)


def in_yale_bbox(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    lat_min, lat_max, lon_min, lon_max = YALE_BBOX
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


class LocationResolver:
    def __init__(self, path: Path):
        data = yaml.safe_load(path.read_text())
        self.areas: dict[str, str] = data["areas"]
        self.buildings: dict[str, Building] = {}
        self._by_name: dict[str, str] = {}
        self._rooms: dict[str, tuple[str, str]] = {}  # normalized sub-venue -> (building id, room name)
        self._by_address: dict[str, str] = {}
        self._room_codes: dict[str, str] = {}
        for b in data["buildings"]:
            if b["area"] not in self.areas:
                raise ValueError(f"building {b['id']} has unknown area {b['area']!r}")
            self.buildings[b["id"]] = Building(b["id"], b["name"], b["area"], b.get("lat"), b.get("lon"))
            for name in [b["name"], *b.get("aliases", [])]:
                self._by_name.setdefault(norm_name(name), b["id"])
            for room in b.get("rooms", []):
                self._rooms[norm_name(room)] = (b["id"], room)
            for addr in b.get("addresses", []):
                self._by_address.setdefault(norm_address(addr), b["id"])
            for code in b.get("room_codes", []):
                self._room_codes[code.upper()] = b["id"]
        self._room_re = re.compile(
            r"^(" + "|".join(map(re.escape, self._room_codes)) + r")[\s-]*(?:room\s*|rm\.?\s*)?([A-Z]?\d+[A-Z]?)$",
            re.IGNORECASE,
        )

    def _match_one(self, name: str) -> tuple[str | None, str | None]:
        if m := self._room_re.match(name):
            return self._room_codes[m.group(1).upper()], m.group(2)
        if hit := self._rooms.get(norm_name(name)):
            return hit
        if (m := _TRAILING_ROOM.match(name)) and (bid := self._match_one(m.group(1))[0]):
            return bid, f"Room {m.group(2)}"
        return self._by_name.get(norm_name(name)) or self._by_address.get(norm_address(name) or ""), None

    def _match(self, name: str | None, address: str | None) -> tuple[str | None, str | None]:
        """Return (building id, room) from the venue name and address alone."""
        name = (name or "").strip()
        bid, room = self._match_one(name)
        if bid:
            return bid, room
        segments = [s.strip() for s in name.split(",") if s.strip()]
        for i, seg in enumerate(segments):
            bid, room = self._match_one(seg)
            if bid:
                # Whatever precedes the building is usually the room: "Room 101, Kroon Hall".
                return bid, room or ", ".join(s for s in segments[:i] if not _CITY_SEGMENT.match(s)) or None
        for candidate in (address, name):
            if candidate and (key := norm_address(candidate)) and (bid := self._by_address.get(key)):
                return bid, None
        return None, None

    def resolve(
        self,
        name: str | None,
        address: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        virtual: bool = False,
    ) -> LocationMatch:
        key = norm_name(name or "")
        # Localist marks virtual-only events explicitly; the venue named is just the host.
        if virtual or key in ONLINE_NAMES:
            return LocationMatch(None, "online")

        bid, room = self._match(name, address)
        if bid:
            return LocationMatch(bid, self.buildings[bid].area, room)
        if key in UNKNOWN_NAMES and not in_yale_bbox(lat, lon):
            return LocationMatch(None, None)

        if in_yale_bbox(lat, lon):
            nearest = min(
                (b for b in self.buildings.values() if b.lat is not None),
                key=lambda b: _meters(lat, lon, b.lat, b.lon),
            )
            if _meters(lat, lon, nearest.lat, nearest.lon) <= NEAREST_MAX_METERS:
                return LocationMatch(None, nearest.area)
            return LocationMatch(None, "off-campus")

        # Outside the box: only call it off-campus when the address names a city (so the geocode
        # is probably right); a bare "17 Prospect Street" geocoded to Brooklyn is just unknown.
        if lat is not None and address and "," in address:
            return LocationMatch(None, "off-campus")
        return LocationMatch(None, None)
