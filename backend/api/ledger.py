"""GET /api/positions?as_of=YYYY-MM-DD : one ledger across the three banks.

One row per security (by ISIN; unresolved names get their own row until a
human maps them), with each bank's holding and the source line behind it.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from flask import Blueprint, jsonify, request

from api.database import get_db
from api.errors import BadRequest
from api.serialize import source
from api.state import current_state
from pipeline.pnl import money
from pipeline.reconcile import security_key

bp = Blueprint("ledger", __name__)

BANK_NAMES = {"harbour": "Harbour Private Bank", "meridian": "Meridian Wealth", "straits": "Straits Custody"}


@bp.get("/positions")
def positions():
    state = current_state(get_db())
    snaps = state["snapshots"]
    dates = sorted({s.as_of_date for s in snaps})
    try:
        as_of = date.fromisoformat(request.args["as_of"]) if "as_of" in request.args else dates[-1]
    except ValueError:
        raise BadRequest("as_of must be YYYY-MM-DD", {"available": [d.isoformat() for d in dates]}) from None
    if as_of not in dates:
        raise BadRequest(f"no statements as of {as_of}", {"available": [d.isoformat() for d in dates]})

    sm = state["secmaster"]
    unevidenced = {(u["account_id"], u["isin"]) for u in state["payoff"]["unevidenced_positions"]}
    rows: dict[str, dict] = defaultdict(lambda: {"holdings": [], "quantity": Decimal(0),
                                                 "market_value_usd": Decimal(0)})
    for s in snaps:
        if s.as_of_date != as_of:
            continue
        key = security_key(s)
        sec = sm.securities.get(s.isin) if s.isin else None
        row = rows[key]
        row.update({"key": key, "isin": s.isin, "resolved": sec is not None,
                    "name": sec.name if sec else s.security_name,
                    "asset_class": sec.asset_class.value if sec else None,
                    "currency": s.currency})
        mv_usd = money(s.market_value * s.fx_to_base)
        row["quantity"] += s.quantity
        row["market_value_usd"] += mv_usd
        row["holdings"].append({
            "account_id": s.account_id, "bank": s.source.bank, "bank_name": BANK_NAMES.get(s.source.bank),
            "name_at_bank": s.security_name, "quantity": s.quantity, "price": s.price,
            "market_value": s.market_value, "currency": s.currency, "fx_to_base": s.fx_to_base,
            "market_value_usd": mv_usd, "basis_unevidenced": (s.account_id, s.isin) in unevidenced,
            "source": source(s.source)})

    items = sorted(rows.values(), key=lambda r: -r["market_value_usd"])
    return jsonify({
        "as_of": as_of.isoformat(),
        "available_dates": [d.isoformat() for d in dates],
        "base_currency": "USD",
        "total_market_value_usd": sum((r["market_value_usd"] for r in items), Decimal(0)),
        "items": items,
    })