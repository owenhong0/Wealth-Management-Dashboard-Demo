"""GET /api/payoff: the numbers clean books change, now vs with no decisions applied."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify

from api.database import get_db
from api.state import current_state

bp = Blueprint("payoff", __name__)

HEADLINE = ["taxable_account_by_account_usd", "taxable_consolidated_usd", "tax_account_by_account_usd",
            "tax_consolidated_usd", "unevidenced_market_value_usd"]


def payoff_body(state: dict[str, Any]) -> dict[str, Any]:
    current, baseline = state["payoff"], state["payoff_baseline"]
    return {
        "current": current,
        "baseline": baseline,
        # How much the decisions so far have changed each headline number (negative = lower).
        "change": {k: current[k] - baseline[k] for k in HEADLINE},
        # What consolidating saves versus filing account by account, right now.
        "consolidation_saves_tax_usd": current["tax_account_by_account_usd"] - current["tax_consolidated_usd"],
    }


@bp.get("/payoff")
def get_payoff():
    return jsonify(payoff_body(current_state(get_db())))