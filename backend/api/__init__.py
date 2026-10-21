"""Flask REST API over the pipeline's tables.

    cd backend && source .venv/bin/activate
    flask --app api run --port 5001 --debug

`flask --app api` looks in this package for create_app() and calls it.
"""
import os
from typing import Callable

from flask import Flask
from flask_cors import CORS
from sqlalchemy.orm import Session

from api.database import init_db
from api.errors import register_error_handlers
from api.serialize import ApiJSONProvider

DEFAULT_CORS_ORIGINS = "http://localhost:3000"


def create_app(session_factory: Callable[[], Session] | None = None) -> Flask:
    """Build the app. Tests can pass their own session_factory."""
    app = Flask(__name__)
    app.json = ApiJSONProvider(app)
    app.config["ALLOW_WRITES"] = os.getenv("ALLOW_WRITES", "true").strip().lower() not in {"false", "0", "no"}

    if session_factory is None:
        from db import SessionLocal, create_tables

        create_tables()  # first boot before any pipeline run still works
        session_factory = SessionLocal
    init_db(app, session_factory)
    register_error_handlers(app)

    origins = os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    CORS(app, resources={r"/api/*": {"origins": [o.strip() for o in origins.split(",") if o.strip()]}})

    # Imported here to avoid a circular import with the `api` package.
    # Imported here to avoid a circular import with the `api` package.
    from api.exceptions import bp as exceptions_bp
    from api.health import bp as health_bp
    from api.ledger import bp as ledger_bp
    from api.payoff import bp as payoff_bp
    from api.sources import bp as sources_bp

    for bp in (health_bp, exceptions_bp, payoff_bp, ledger_bp, sources_bp):
        app.register_blueprint(bp, url_prefix="/api")
    return app