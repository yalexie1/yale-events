import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from yale_events.models import Base

DEFAULT_DB_URL = "sqlite:///data/events.db"


def make_engine(url: str | None = None):
    url = url or os.environ.get("YEV_DB_URL", DEFAULT_DB_URL)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(make_engine(url), expire_on_commit=False)
