from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NEW_HAVEN = ZoneInfo("America/New_York")
ONGOING_MIN_DAYS = 14


def clean_times(start: datetime, end: datetime | None, all_day: bool) -> tuple[datetime, datetime | None]:
    """Make times tz-aware and consistent. Naive times are assumed to be New Haven local time."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=NEW_HAVEN)
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=NEW_HAVEN)
    if all_day:
        local = start.astimezone(NEW_HAVEN)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = None
    if end is not None and (end <= start or end - start > timedelta(days=31)):
        end = None  # bogus or placeholder end times; better to show none than a wrong one
    return start, end


def is_ongoing(first_date, last_date, all_day: bool, categories: list[str]) -> bool:
    """A long-running series (exhibition, open gallery hours) rather than a discrete event."""
    if first_date is None or last_date is None:
        return False
    long_running = (last_date - first_date).days >= ONGOING_MIN_DAYS
    return long_running and (all_day or "exhibitions" in categories)
