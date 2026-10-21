"""The exception queue and the human decisions on it.

GET  /api/exceptions                   the queue, with status from the decision log
POST /api/exceptions/<id>/approve      {"decided_by"?, "note"?}  apply the proposed fix
POST /api/exceptions/<id>/override     {"decided_by"?, "note"}   reject it; a note is required
GET  /api/decisions                    the audit log, newest first (voided entries included)
POST /api/reset                        void every active decision (demo reset; log is kept)

Writes return the updated exception and payoff, so the UI can redraw in one round trip.
"""
from __future__ import annotations

from collections import Counter

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select, update

from api.database import get_db
from api.errors import APIError, BadRequest, NotFound
from api.payoff import payoff_body
from api.serialize import decision, exception
from api.state import current_state
from models import Decision, DecisionStatus
from models.base import utcnow

bp = Blueprint("exceptions", __name__)

MAX_NOTE = 2000


def _require_writes() -> None:
    if not current_app.config["ALLOW_WRITES"]:
        raise APIError(403, "This deployment is read-only (ALLOW_WRITES=false).")


def _queue_body(state) -> dict:
    items = [exception(e) for e in state["exceptions"]]
    return {"items": items, "counts": dict(Counter(e["status"] for e in items)), "total": len(items)}


@bp.get("/exceptions")
def list_exceptions():
    return jsonify(_queue_body(current_state(get_db())))


@bp.get("/exceptions/<exception_id>")
def get_exception(exception_id: str):
    match = next((e for e in current_state(get_db())["exceptions"] if e.exception_id == exception_id), None)
    if match is None:
        raise NotFound(f"no exception {exception_id!r}")
    return jsonify(exception(match))


def _decide(exception_id: str, status: DecisionStatus):
    _require_writes()
    body = request.get_json(silent=True) or {}
    note = (body.get("note") or "").strip() or None
    decided_by = (body.get("decided_by") or "").strip() or "reviewer"
    if note and len(note) > MAX_NOTE:
        raise BadRequest(f"note is longer than {MAX_NOTE} characters")
    if status is DecisionStatus.OVERRIDDEN and not note:
        raise BadRequest("an override needs a note saying why", {"field": "note"})

    db = get_db()
    state = current_state(db)
    target = next((e for e in state["exceptions"] if e.exception_id == exception_id), None)
    if target is None:
        raise NotFound(f"no exception {exception_id!r}")

    db.add(Decision(exception_id=exception_id, exception_type=target.type.value, status=status,
                    decided_by=decided_by[:120], note=note, summary=target.summary,
                    proposed_fix=target.proposed_fix, fix=target.fix))
    db.commit()

    state = current_state(db)  # recomputed with the new decision applied
    updated = next(e for e in state["exceptions"] if e.exception_id == exception_id)
    return jsonify({"exception": exception(updated), "queue": _queue_body(state)["counts"],
                    "payoff": payoff_body(state)}), 201


@bp.post("/exceptions/<exception_id>/approve")
def approve(exception_id: str):
    return _decide(exception_id, DecisionStatus.APPROVED)


@bp.post("/exceptions/<exception_id>/override")
def override(exception_id: str):
    return _decide(exception_id, DecisionStatus.OVERRIDDEN)


@bp.get("/decisions")
def list_decisions():
    rows = get_db().scalars(select(Decision).order_by(Decision.id.desc())).all()
    return jsonify({"items": [decision(d) for d in rows], "total": len(rows)})


@bp.post("/reset")
def reset():
    _require_writes()
    db = get_db()
    result = db.execute(update(Decision).where(Decision.voided_at.is_(None)).values(voided_at=utcnow()))
    db.commit()
    return jsonify({"voided": result.rowcount, "payoff": payoff_body(current_state(db))})