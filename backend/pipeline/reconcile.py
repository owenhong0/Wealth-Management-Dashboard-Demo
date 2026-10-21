"""Reconcile: find everything a human must look at. Deterministic; no model.

Four checks, each producing exceptions that cite their exact source lines:

NAME_CONFLICT       a bank's security name the securities master can't map.
                    Fix: map the name to the suggested ISIN.
BASIS_MISSING       units arrived free with no cost, and a delivery of the same
                    security, quantity and date left another account.
                    Fix: carry the delivered FIFO lots' cost over.
UNMATCHED_TRANSFER  units arrived free and no delivery matches anywhere.
                    No automatic fix: basis stays unevidenced until evidenced.
POSITION_BREAK      prior statement + the year's trades != this statement.
                    No automatic fix: never plugged, only flagged.

Exception ids are stable across runs (built from account, security and date),
so a human's decision stays attached to the same exception after a re-run.
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import Any

from pipeline.schema import QTY_SIGN, ExceptionType, PositionSnapshot, ReconException, Transaction, TxnType


def security_key(r: Transaction | PositionSnapshot) -> str:
    """ISIN when resolved; otherwise the bank's name, so unresolved securities still roll forward."""
    return r.isin or f"NAME:{r.security_name}"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")[:24]


def _qty(x: Decimal) -> str:
    return f"{x:,.0f}"


# --- NAME_CONFLICT ------------------------------------------------------------------------------


def name_conflicts(unresolved: list[dict[str, Any]], sm) -> list[ReconException]:
    out = []
    for u in unresolved:
        s = u["suggestion"]
        records = u["records"]
        txn_ids = [r.txn_id for r in records if isinstance(r, Transaction)]
        target = sm.securities.get(s.isin) if s else None
        out.append(ReconException(
            exception_id=f"NC-{u['account_id']}-{_slug(u['security_name'])}",
            type=ExceptionType.NAME_CONFLICT,
            account_id=u["account_id"],
            isin=s.isin if s else None,
            summary=f"{u['bank'].title()} calls a security \"{u['security_name']}\", which the securities "
                    f"master doesn't know ({len(records)} records use this name).",
            details={"bank": u["bank"], "security_name": u["security_name"], "records": len(records),
                     "suggested_isin": s.isin if s else None,
                     "suggested_name": target.name if target else None,
                     "known_aliases": list(target.aliases) if target else [],
                     "match_reasons": list(s.reasons) if s else []},
            affected_txn_ids=txn_ids,
            evidence=[r.source for r in records],
            proposed_fix=(f"Map this name to {target.name} ({s.isin}): {'; '.join(s.reasons)}."
                          if s and target else "No candidate found; add the security to the master."),
            fix={"action": "map_alias", "security_name": u["security_name"], "isin": s.isin} if s else None,
            confidence=s.score if s else None,
        ))
    return out


# --- transfers ---------------------------------------------------------------------------------------


def transfer_exceptions(txns: list[Transaction]) -> list[ReconException]:
    outs = [t for t in txns if t.type is TxnType.TRANSFER_OUT]
    out = []
    for t in txns:
        if t.type is not TxnType.TRANSFER_IN or t.cost_basis is not None:
            continue
        key = security_key(t)
        match = next((o for o in outs if security_key(o) == key and o.trade_date == t.trade_date
                      and o.quantity == t.quantity and o.account_id != t.account_id), None)
        base_id = f"{t.account_id}-{_slug(key)}-{t.trade_date:%Y%m%d}"
        common = dict(account_id=t.account_id, isin=t.isin)
        if match:
            out.append(ReconException(
                exception_id=f"BM-{base_id}",
                type=ExceptionType.BASIS_MISSING,
                summary=f"{_qty(t.quantity)} {t.security_name} arrived at {t.account_id} on {t.trade_date:%d %b %Y} "
                        f"with no cost basis.",
                details={"quantity": str(t.quantity), "date": t.trade_date.isoformat(),
                         "received_by": t.account_id, "received_from": t.counterparty,
                         "delivered_by": match.account_id, "delivered_to": match.counterparty,
                         "delivery_txn_id": match.txn_id, "receipt_txn_id": t.txn_id},
                affected_txn_ids=[match.txn_id, t.txn_id],
                evidence=[match.source, t.source],
                proposed_fix=f"Carry over the cost of the FIFO lots {match.account_id} delivered: same security, "
                             f"same quantity ({_qty(t.quantity)}), same date"
                             + (f", and the receipt names {t.counterparty} as the sender." if t.counterparty else "."),
                fix={"action": "carry_basis", "from_txn_id": match.txn_id, "to_txn_id": t.txn_id},
                confidence=0.95,
                **common,
            ))
        else:
            out.append(ReconException(
                exception_id=f"UT-{base_id}",
                type=ExceptionType.UNMATCHED_TRANSFER,
                summary=f"{_qty(t.quantity)} {t.security_name} arrived at {t.account_id} on {t.trade_date:%d %b %Y} "
                        f"from {t.counterparty or 'an unnamed custodian'}, with no cost and no matching delivery "
                        f"in any account we hold.",
                details={"quantity": str(t.quantity), "date": t.trade_date.isoformat(),
                         "received_by": t.account_id, "received_from": t.counterparty,
                         "receipt_txn_id": t.txn_id},
                affected_txn_ids=[t.txn_id],
                evidence=[t.source],
                proposed_fix=f"Request the delivery statement and original trade confirmations from "
                             f"{t.counterparty or 'the delivering custodian'}. Until then the cost basis stays "
                             f"unevidenced (treated as zero for tax).",
                fix=None,
                **common,
            ))
    return out


# --- POSITION_BREAK -------------------------------------------------------------------------------


def position_breaks(txns: list[Transaction], snaps: list[PositionSnapshot]) -> list[ReconException]:
    """Roll forward each account and security from one year-end statement to the next."""
    by_date: dict[tuple[str, str], dict[int, PositionSnapshot]] = defaultdict(dict)
    statement_years: dict[str, set[int]] = defaultdict(set)
    for s in snaps:
        if (s.as_of_date.month, s.as_of_date.day) == (12, 31):
            by_date[(s.account_id, security_key(s))][s.as_of_date.year] = s
            statement_years[s.account_id].add(s.as_of_date.year)
    moves: dict[tuple[str, str, int], list[Transaction]] = defaultdict(list)
    for t in txns:
        if t.type in QTY_SIGN:
            moves[(t.account_id, security_key(t), t.trade_date.year)].append(t)

    out = []
    keys = set(by_date) | {(a, k) for (a, k, _) in moves}
    for account, key in sorted(keys):
        years = sorted(statement_years[account])
        for y in years:
            opening_snap = by_date[(account, key)].get(y - 1)
            closing_snap = by_date[(account, key)].get(y)
            opening = opening_snap.quantity if opening_snap else Decimal(0)
            year_moves = moves.get((account, key, y), [])
            net = sum((QTY_SIGN[t.type] * t.quantity for t in year_moves), Decimal(0))
            closing = closing_snap.quantity if closing_snap else Decimal(0)
            if opening + net == closing:
                continue
            ref = closing_snap or opening_snap or year_moves[0]
            name = ref.security_name
            diff = closing - (opening + net)
            out.append(ReconException(
                exception_id=f"PB-{account}-{_slug(key)}-{y}",
                type=ExceptionType.POSITION_BREAK,
                account_id=account,
                isin=ref.isin,
                summary=f"{name} at {account}, {y}: opening {_qty(opening)} + trades {net:+,.0f} = "
                        f"{_qty(opening + net)}, but the statement shows {_qty(closing)} ({diff:+,.0f}).",
                details={"year": y, "opening": str(opening), "net_trades": str(net),
                         "expected_closing": str(opening + net), "statement_closing": str(closing),
                         "difference": str(diff)},
                affected_txn_ids=[t.txn_id for t in year_moves],
                evidence=[s.source for s in (opening_snap,) if s] + [t.source for t in year_moves]
                         + [s.source for s in (closing_snap,) if s],
                proposed_fix=f"Do not plug the difference. Ask {ref.source.bank.title()} for trade confirmations "
                             f"for {name} in {y}: {abs(diff):,.0f} units "
                             f"{'left' if diff < 0 else 'arrived'} with no transaction on the statement.",
                fix=None,
            ))
    return out


def reconcile(event: dict[str, Any]) -> dict[str, Any]:
    """Pipeline step. Runs after normalize; exceptions are found before any decision is applied."""
    txns, snaps = event["transactions"], event["snapshots"]
    exceptions = (name_conflicts(event.get("unresolved_names", []), event["secmaster"])
                  + transfer_exceptions(txns)
                  + position_breaks(txns, snaps))
    event["exceptions"] = exceptions
    by_type: dict[str, int] = defaultdict(int)
    for e in exceptions:
        by_type[e.type.value] += 1
    event["count_exceptions"] = dict(by_type)
    return event