from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NEW_HAVEN = ZoneInfo("America/New_York")
ONGOING_MIN_DAYS = 14


def clean_times(
    start: datetime, end: datetime | None, all_day: bool, long_ok: bool = False
) -> tuple[datetime, datetime | None]:
    """Make times tz-aware and consistent. Naive times are assumed to be New Haven local time."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=NEW_HAVEN)
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=NEW_HAVEN)
    if all_day:
        local = start.astimezone(NEW_HAVEN)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = None
    if end is not None and (end <= start or (end - start > timedelta(days=31) and not long_ok)):
        end = None  # bogus or placeholder end times; better to show none than a wrong one
    return start, end


SHORT_EVENT = timedelta(hours=3)
SPARSE_PER_WEEK = 3


def mark_sparse_series(events: list) -> None:
    """Timed occurrences without an end time, in a series that meets under three times a week in the
    fetched window (a weekly gallery tour slot), are discrete events even if categorized as exhibitions;
    open hours recur almost daily."""
    by_series: dict[str, list] = {}
    for e in events:
        if e.series_id and not e.all_day and e.end is None and e.ongoing is None:
            by_series.setdefault(e.series_id, []).append(e)
    for occurrences in by_series.values():
        days = {e.start.date() for e in occurrences}
        weeks = max(1.0, ((max(days) - min(days)).days + 1) / 7)
        if len(days) / weeks < SPARSE_PER_WEEK:
            for e in occurrences:
                e.ongoing = False


def is_ongoing(
    first_date, last_date, all_day: bool, categories: list[str], start: datetime | None = None, end: datetime | None = None
) -> bool:
    """A long-running series (exhibition, open gallery hours) rather than a discrete event. A timed
    occurrence under three hours (a weekly gallery tour, a studio class) is a discrete event."""
    if first_date is None or last_date is None:
        return False
    if not all_day and start and end and end - start < SHORT_EVENT:
        return False
    long_running = (last_date - first_date).days >= ONGOING_MIN_DAYS
    return long_running and (all_day or "exhibitions" in categories)
