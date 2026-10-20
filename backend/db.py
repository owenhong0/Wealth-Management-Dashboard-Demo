"""Engine and session factory shared by the API and the pipeline."""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import DATABASE_URL

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def create_tables() -> None:
    """Create any missing tables. Safe to call repeatedly."""
    from models import Base  # importing registers every table

    Base.metadata.create_all(engine)


def reset_schema() -> None:
    """Drop and recreate every table. The pipeline rebuilds from data/generated/."""
    from models import Base

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on error, always close."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()