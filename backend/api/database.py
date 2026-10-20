"""One SQLAlchemy session per request.

The session is created on first use, so a request that never touches the
database never opens a connection, and a broken database surfaces inside
the view (where /api/health can report it). It is closed on teardown.
Views that write call get_db().commit() themselves; anything left
uncommitted is rolled back by close().
"""
from typing import Callable

from flask import Flask, current_app, g
from sqlalchemy.orm import Session

_EXT_KEY = "session_factory"


def init_db(app: Flask, session_factory: Callable[[], Session]) -> None:
    app.extensions[_EXT_KEY] = session_factory
    app.teardown_appcontext(_close_db)


def get_db() -> Session:
    """This request's session, created on first call."""
    if "db" not in g:
        g.db = current_app.extensions[_EXT_KEY]()
    return g.db


def _close_db(exc: BaseException | None) -> None:
    """Runs after every request, including ones that raised."""
    session: Session | None = g.pop("db", None)
    if session is None:
        return
    if exc is not None:
        session.rollback()
    session.close()