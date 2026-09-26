"""Text cleanup shared by adapters that read HTML or loosely formatted feeds."""

import re
from html import unescape

_BLOCK_TAGS = re.compile(r"(?i)<\s*(br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>")
_TAGS = re.compile(r"(?s)<[^>]+>")
_SCRIPTS = re.compile(r"(?is)<(script|style)\b.*?</\1>")
# Field labels some feeds put inside values: "Location \nSLB Room 127", "Description \n\nText".
_LABEL = re.compile(r"^\s*(location|description|where|details)\s*:?\s*\n", re.IGNORECASE)


def html_to_text(s: str | None) -> str | None:
    """Strip tags and entities, keeping paragraph breaks. Also fixes double-escaped text ("&amp;amp;")."""
    if not s:
        return None
    s = _SCRIPTS.sub(" ", s)
    s = _BLOCK_TAGS.sub("\n", s)
    s = unescape(_TAGS.sub(" ", s))
    if re.search(r"&(amp|lt|gt|quot|#\d+);", s):
        s = unescape(s)
    lines = [" ".join(line.split()) for line in s.replace("\xa0", " ").splitlines()]
    s = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return s or None


def clean_field(s: str | None) -> str | None:
    """A short single-line value (title, location): drop a leading label, tags, and extra whitespace."""
    if not s:
        return None
    s = _LABEL.sub("", str(s))
    text = html_to_text(s)
    return " ".join(text.split()) if text else None


def clean_description(s: str | None) -> str | None:
    return html_to_text(_LABEL.sub("", str(s))) if s else None
