import hashlib
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, TypeDecorator, UniqueConstraint,
)  # fmt: skip
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UTCDateTime(TypeDecorator):
    """Stores datetimes as naive UTC (SQLite has no tz support) and returns them tz-aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to UTCDateTime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None else None


def utcnow() -> datetime:
    return datetime.now(UTC)


def event_id(source_id: str, source_event_id: str) -> str:
    return hashlib.sha1(f"{source_id}:{source_event_id}".encode()).hexdigest()[:16]


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("source_id", "source_event_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    source_event_id: Mapped[str] = mapped_column(String)

    title: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    start: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)

    location_name: Mapped[str | None] = mapped_column(String)
    room: Mapped[str | None] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(String)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    virtual: Mapped[bool] = mapped_column(Boolean, default=False)

    url: Mapped[str | None] = mapped_column(String)
    image_url: Mapped[str | None] = mapped_column(String)

    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    audience: Mapped[list[str]] = mapped_column(JSON, default=list)
    groups: Mapped[list[str]] = mapped_column(JSON, default=list)

    free: Mapped[bool | None] = mapped_column(Boolean)
    cost: Mapped[str | None] = mapped_column(String)
    # Cancelled by the source, or no longer listed in the source feed.
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)

    series_id: Mapped[str | None] = mapped_column(String, index=True)
    series_first_date: Mapped[date | None] = mapped_column(Date)
    series_last_date: Mapped[date | None] = mapped_column(Date)

    # Derived by normalize.Normalizer from the fields above.
    location_id: Mapped[str | None] = mapped_column(String, index=True)
    area: Mapped[str | None] = mapped_column(String, index=True)
    free_food: Mapped[bool] = mapped_column(Boolean, default=False)
    # Long-running series (exhibitions) that would otherwise flood the feed with one row per day.
    ongoing: Mapped[bool] = mapped_column(Boolean, default=False)
    category_rows: Mapped[list["EventCategory"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="EventCategory.category"
    )
    categories: AssociationProxy[list[str]] = association_proxy(
        "category_rows", "category", creator=lambda c: EventCategory(category=c)
    )

    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class EventCategory(Base):
    __tablename__ = "event_categories"

    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    category: Mapped[str] = mapped_column(String, primary_key=True, index=True)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String, default="running")  # running | ok | error
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    marked_stale: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
