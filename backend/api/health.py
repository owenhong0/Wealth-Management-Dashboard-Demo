"""GET /api/health: is the API up, can it reach the database, and what ran last."""
from flask import Blueprint, current_app, jsonify
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from api.database import get_db
from api.serialize import run
from models import PipelineRun

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    try:
        db = get_db()
        db.execute(text("SELECT 1"))
        latest = db.scalars(select(PipelineRun).order_by(PipelineRun.id.desc()).limit(1)).first()
    except SQLAlchemyError as exc:
        current_app.logger.warning("health check: database unreachable: %s", exc)
        return jsonify(status="error", database="unreachable"), 503

    return jsonify(
        status="ok",
        database="ok",
        latest_run=run(latest) if latest is not None else None,
    )