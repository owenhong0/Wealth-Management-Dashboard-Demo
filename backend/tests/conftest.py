"""Tests use their own SQLite file in a temp directory, never data/basis_trail.db."""
import os
import tempfile
from pathlib import Path

import pytest

# Must happen before config.py / db.py are imported anywhere: they read it once.
_TMP = Path(tempfile.mkdtemp(prefix="basis_trail_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"


@pytest.fixture()
def fresh_db():
    from api.state import clear_cache
    from db import reset_schema

    reset_schema()
    clear_cache()


@pytest.fixture()
def app(fresh_db):
    from api import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client(app):
    return app.test_client()