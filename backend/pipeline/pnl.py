"""The payoff numbers: what clean books are worth, in USD.

For the tax year:
- realized gain per account (FIFO; unevidenced basis counts as zero cost)
- "account by account": each account filed separately, losses can't offset
  another account's gains  -> sum of the positive accounts
- "consolidated": one set of books, losses offset gains -> max(total, 0)
- tax at 20% on each (China Announcement No. 21 rate on offshore trust events)
- unevidenced market value: year-end value of units still held with no
  evidenced cost

Every figure keeps the ids of what built it, for the drill-down.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pipeline.basis import Disposal, Lot, build_lots
from pipeline.reconcile import security_key
from pipeline.schema import PositionSnapshot, Transaction

TAX_YEAR = 2025
TAX_RATE = Decimal("0.20")


def money(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _lot_view(l: Lot) -> dict[str, Any]:
    return {"acquired": l.acquired.isoformat(), "quantity": l.quantity,
            "unit_cost": l.unit_cost, "currency": l.currency, "fx": l.fx,
            "cost_usd": money(l.cost_usd) if l.cost_usd is not None else None,
            "source": l.source.label() if l.source else None, "via": l.via}


def payoff(txns: list[Transaction], snaps: list[PositionSnapshot], carry: dict[str, str],
           tax_year: int = TAX_YEAR) -> dict[str, Any]:
    lots, disposals = build_lots(txns, carry)
    accounts = sorted({t.account_id for t in txns})

    year_disposals = [d for d in disposals if d.txn.trade_date.year == tax_year]
    by_account: dict[str, Decimal] = {a: Decimal(0) for a in accounts}
    for d in year_disposals:
        by_account[d.txn.account_id] += d.gain_usd
    account_by_account = sum((max(g, Decimal(0)) for g in by_account.values()), Decimal(0))
    consolidated = max(sum(by_account.values(), Decimal(0)), Decimal(0))

    year_end = date(tax_year, 12, 31)
    snap_at = {(s.account_id, security_key(s)): s for s in snaps if s.as_of_date == year_end}
    unevidenced = []
    for (account, key), open_lots in sorted(lots.items()):
        qty = sum((l.quantity for l in open_lots if l.unit_cost is None), Decimal(0))
        snap = snap_at.get((account, key))
        if not qty or snap is None or not snap.quantity:
            continue
        value = snap.market_value * snap.fx_to_base * qty / snap.quantity
        unevidenced.append({"account_id": account, "isin": snap.isin, "security_name": snap.security_name,
                            "quantity": qty, "market_value_usd": money(value),
                            "sources": [l.source.label() for l in open_lots if l.unit_cost is None and l.source]
                                       + [snap.source.label()]})

    return {
        "tax_year": tax_year,
        "tax_rate": TAX_RATE,
        "cost_basis_method": "FIFO; unevidenced basis treated as zero cost",
        "realized_gain_by_account_usd": {a: money(g) for a, g in by_account.items()},
        "taxable_account_by_account_usd": money(account_by_account),
        "taxable_consolidated_usd": money(consolidated),
        "tax_account_by_account_usd": money(account_by_account * TAX_RATE),
        "tax_consolidated_usd": money(consolidated * TAX_RATE),
        "unevidenced_market_value_usd": money(sum((u["market_value_usd"] for u in unevidenced), Decimal(0))),
        "unevidenced_positions": unevidenced,
        "disposals": [{
            "txn_id": d.txn.txn_id, "account_id": d.txn.account_id, "isin": d.txn.isin,
            "security_name": d.txn.security_name, "date": d.txn.trade_date.isoformat(),
            "quantity": d.txn.quantity, "proceeds_usd": money(d.proceeds_usd),
            "cost_usd": money(d.evidenced_cost_usd), "gain_usd": money(d.gain_usd),
            "unevidenced_quantity": d.unevidenced_quantity, "source": d.txn.source.label(),
            "lots": [_lot_view(l) for l in d.lots],
        } for d in year_disposals],
    }


def basis_and_pnl(event: dict[str, Any]) -> dict[str, Any]:
    """Pipeline step. `payoff` reflects approved decisions; `payoff_baseline` reflects none."""
    txns, snaps = event["transactions"], event["snapshots"]
    event["payoff"] = payoff(txns, snaps, event.get("carry_basis", {}))
    event["payoff_baseline"] = payoff(txns, snaps, {})
    event["count_disposals_in_tax_year"] = len(event["payoff"]["disposals"])
    return event