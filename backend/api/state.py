"""The books, as of the current decisions.

Every request gets the pipeline's result with the active decisions applied.
The pipeline takes ~0.2 s, so results are cached by the decision log's state:
a new decision or a reset changes the key and forces a recompute.
"""
from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.serialize import iso
from models import Decision
from pipeline.run import run_steps

_cache: dict[tuple, dict[str, Any]] = {}
_lock = threading.Lock()


def active_decisions(db: Session) -> list[Decision]:
    """The latest non-voided decision per exception."""
    rows = db.scalars(select(Decision).where(Decision.voided_at.is_(None)).order_by(Decision.id)).all()
    latest: dict[str, Decision] = {}
    for d in rows:
        latest[d.exception_id] = d  # later rows win
    return list(latest.values())


def current_state(db: Session) -> dict[str, Any]:
    decisions = active_decisions(db)
    key = tuple(sorted((d.exception_id, d.id, d.status.value) for d in decisions))
    with _lock:
        if key not in _cache:
            _cache.clear()  # keep only the latest state
            _cache[key] = run_steps({"decisions": {
                d.exception_id: {"status": d.status.value, "decided_by": d.decided_by,
                                 "decided_at": iso(d.decided_at), "note": d.note}
                for d in decisions}})
        return _cache[key]


def clear_cache() -> None:
    with _lock:
        _cache.clear()