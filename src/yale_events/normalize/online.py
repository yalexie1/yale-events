"""Separate online-meeting access (join links, meeting IDs, passcodes) from the venue in a location string.

Some calendars put the whole Zoom or Teams invitation in the location field. The API shows the in-person
part as the venue and the rest as online access, so a join link or passcode never reads as a place.
"""

import re
from dataclasses import dataclass

URL_RE = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]]")
_ONLINE = re.compile(
    r"https?://|\b(?:zoom|teams|webex|google meet|online|virtual|webinar|meeting id|passcode|password)\b", re.I
)
# Just a label, with nothing to hand on: "Online", "via Zoom", "Online (Zoom)".
_LABEL_ONLY = re.compile(
    r"^(?:via\s+)?(?:online(?:\s+event)?|virtual|zoom|(?:microsoft\s+)?teams|webex)(?:\s*\((?:zoom|teams|webex)\))?\.?$", re.I
)
# Instructions rather than a place: "email contact for link", "Please call 203-... for more information".
_INSTRUCTION = re.compile(r"^(?:please|registration|register|rsvp|contact|e-?mail|visit|sign up|call)\b", re.I)
# "... Also in-person, YSM Campus, FMP 132 Conference Room"
_ALSO_IN_PERSON = re.compile(r"\s*\balso\s+in[- ]person\b[,:]?\s*(?P<venue>.+)$", re.I)
# "Hybrid (SLB Room 109 and Zoom)"
_HYBRID = re.compile(r"^hybrid\s*\((?P<venue>.+?)\s+(?:and|&|\+|or)\s+(?:zoom|teams|online)\)$", re.I)
# "DL 501 or Zoom", "Burke + Zoom, New Haven, CT"
_OR_ONLINE = re.compile(r"^(?P<venue>[^:/]+?)\s+(?:or|and|&|\+)\s+(?:zoom|teams|online)\b(?P<rest>.*)$", re.I)


@dataclass(frozen=True)
class VenueSplit:
    venue: str | None  # the in-person place, if any
    online: bool  # the event can be joined online (per the text; instructions alone don't say)
    url: str | None = None  # first link in the access details (join or registration)
    details: str | None = None  # access text as the source gave it: links, meeting ID, passcode, instructions


def split_online(name: str | None) -> VenueSplit:
    if not name or not (name := name.strip()):
        return VenueSplit(None, False)
    if _INSTRUCTION.match(name):
        return VenueSplit(None, bool(_ONLINE.search(name)), m.group(0) if (m := URL_RE.search(name)) else None, name)
    if not _ONLINE.search(name):
        return VenueSplit(name, False)
    if _LABEL_ONLY.match(name):
        return VenueSplit(None, True)
    if m := _HYBRID.match(name):
        return VenueSplit(m["venue"].strip(), True)
    if (m := _OR_ONLINE.match(name)) and not _ONLINE.search(m["venue"]):
        return VenueSplit((m["venue"] + m["rest"]).strip(" ,") or None, True)
    venue = None
    if m := _ALSO_IN_PERSON.search(name):
        venue, name = m["venue"].strip(" ,."), name[: m.start()].strip()
    url = m.group(0) if (m := URL_RE.search(name)) else None
    details = None if not name or _LABEL_ONLY.match(name) else name
    return VenueSplit(venue or None, True, url, details)
