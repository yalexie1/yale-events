import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from yale_events.models import Base

DEFAULT_DB_URL = "sqlite:///data/events.db"


def make_engine(url: str | None = None):
    url = url or os.environ.get("YEV_DB_URL", DEFAULT_DB_URL)
    engine = create_engine(url)
    if engine.dialect.name == "sqlite" and ":memory:" not in url and url != "sqlite://":
        # WAL lets the API keep reading while a scheduled scrape writes.
        @event.listens_for(engine, "connect")
        def _wal(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(engine)
    return engine


def make_session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(make_engine(url), expire_on_commit=False)
