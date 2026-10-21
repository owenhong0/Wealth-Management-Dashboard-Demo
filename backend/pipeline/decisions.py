"""Apply human decisions to the reconciled data.

Decisions come in as {exception_id: {"status": "APPROVED" | "OVERRIDDEN",
"decided_by": ..., "decided_at": ..., "note": ...}}. Only an APPROVED
exception's `fix` changes any data; OVERRIDDEN records that a person rejected
the proposal, and the data stays as the banks reported it.

Exceptions themselves are always found BEFORE decisions are applied (see
reconcile), so the queue keeps every exception, with its status, after approval.
"""
from __future__ import annotations

from typing import Any

from pipeline.schema import ExceptionStatus


def apply_decisions(event: dict[str, Any]) -> dict[str, Any]:
    """Pipeline step. Runs after reconcile, before basis_and_pnl."""
    decisions: dict[str, dict[str, Any]] = event.get("decisions") or {}
    known = {e.exception_id for e in event["exceptions"]}
    aliases: dict[str, str] = {}
    carry: dict[str, str] = {}

    updated = []
    for e in event["exceptions"]:
        d = decisions.get(e.exception_id)
        if d:
            e = e.model_copy(update={"status": ExceptionStatus(d["status"]), "decided_by": d.get("decided_by"),
                                     "decided_at": d.get("decided_at")})
        if e.status is ExceptionStatus.APPROVED and e.fix:
            if e.fix["action"] == "map_alias":
                aliases[e.fix["security_name"]] = e.fix["isin"]
            elif e.fix["action"] == "carry_basis":
                carry[e.fix["to_txn_id"]] = e.fix["from_txn_id"]
        updated.append(e)
    event["exceptions"] = updated

    if aliases:  # an approved name mapping resolves every record that uses the name
        for key in ("transactions", "snapshots"):
            event[key] = [r.model_copy(update={"isin": aliases[r.security_name]})
                          if r.isin is None and r.security_name in aliases else r
                          for r in event[key]]

    event["carry_basis"] = carry
    event["count_decisions_applied"] = sum(1 for k in decisions if k in known)
    event["count_decisions_unknown"] = sorted(k for k in decisions if k not in known)
    return event