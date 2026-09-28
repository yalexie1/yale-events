"""Session dates of a long-running event that a feed lists as one span.

Yale Connect exports a weekly series as a single event from its first session to its last
("Sat, Aug 29 9:00 AM – Sat, Dec 19 12:00 PM"), with the actual schedule only in the text. In order
of preference, the sessions come from:

1. dated lines: "Sep 13: Working with Stress", "Part 2: Synthesizing (Sept. 30)", "Part 5 (Tue 11/10)";
   lines about breaks ("Fall Break", "Recess", "Final Exams Begin") are skipped, and a listed date up to a
   week past the span counts (a closing ceremony the span leaves out);
2. weekdays: "every Thursday", "Sundays, 7 - 8 PM", "(Wednesdays)", minus "no webinars on October 22
   or November 26";
3. "weekly": the weekday of the first session.

Without any of these the schedule is unknown and `session_dates` returns None.
"""

import re
from datetime import date, timedelta

MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
WEEKDAYS = "mon tues wednes thurs fri satur sun".split()
LISTED_GRACE = timedelta(days=7)

_MONTH = r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DAY_NAME = r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s*)?"
_LEADING = re.compile(rf"^{_DAY_NAME}{_MONTH}\s+(?P<day>\d{{1,2}})\s*:\s*(?P<topic>.+)$", re.I)
_TRAILING = re.compile(
    rf"^(?P<topic>.+?)\s*\({_DAY_NAME}(?:{_MONTH}\s+(?P<day>\d{{1,2}})|(?P<m>\d{{1,2}})/(?P<d>\d{{1,2}}))\)\s*$", re.I
)
_BREAK = re.compile(r"\b(break|recess|holiday|exams? begin|no (class|session|meeting|webinar)s?|cancell?ed)\b", re.I)
_WEEKDAY = re.compile(r"\b(?:(every)\s+)?(mon|tues|wednes|thurs|fri|satur|sun)day(s)?\b", re.I)
_SKIP_CLAUSE = re.compile(r"\bno (?:\w+ )?(?:webinars?|sessions?|class(?:es)?|meetings?|talks?) on ([^.\n]+)", re.I)
_MONTH_DAY = re.compile(rf"{_MONTH}\s+(?P<day>\d{{1,2}})\b|\b(?P<m>\d{{1,2}})/(?P<d>\d{{1,2}})\b", re.I)


def session_dates(title: str, description: str | None, first: date, last: date) -> list[tuple[date, str | None]] | None:
    """(date, topic) for each session from `first` to `last`, or None if the text gives no schedule."""
    text = description or ""
    dated = _dated_lines(text, first, last + LISTED_GRACE)
    if len(dated) >= 2:
        return dated
    weekdays = {
        WEEKDAYS.index(m[2].lower())
        for m in _WEEKDAY.finditer(f"{title}\n{text}")
        if m[1] or m[3]  # "every Thursday" or "Thursdays", not a passing "Thursday"
    }
    if not weekdays and re.search(r"\bweekly\b", f"{title}\n{text}", re.I):
        weekdays = {first.weekday()}
    if not weekdays:
        return None
    skipped = {d for clause in _SKIP_CLAUSE.findall(text) for d in _dates_in(clause, first, last)}
    days = (first + timedelta(n) for n in range((last - first).days + 1))
    return [(d, None) for d in days if d.weekday() in weekdays and d not in skipped]


def _dated_lines(text: str, first: date, last: date) -> list[tuple[date, str | None]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        m = _LEADING.match(line) or _TRAILING.match(line)
        if not m or _BREAK.search(m["topic"]):
            continue
        d = _resolve(*_month_day(m), first, last)
        if d and d not in (x for x, _ in out):
            out.append((d, m["topic"].strip(" -–:") or None))
    return sorted(out)


def _dates_in(text: str, first: date, last: date) -> list[date]:
    return [d for m in _MONTH_DAY.finditer(text) if (d := _resolve(*_month_day(m), first, last))]


def _month_day(m: re.Match) -> tuple[int, int]:
    if m["mon"]:
        return MONTHS.index(m["mon"].lower()[:3]) + 1, int(m["day"])
    return int(m["m"]), int(m["d"])


def _resolve(month: int, day: int, first: date, last: date) -> date | None:
    """The date with this month and day between `first` and `last` (the span may cross New Year)."""
    for year in range(first.year, last.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if first <= d <= last:
            return d
    return None
