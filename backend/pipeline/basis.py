"""Cost basis lineage: FIFO lots per account and security.

- BUY opens a lot at the price paid (gross amount / quantity, in the trade
  currency, so bond pricing per 100 needs no special case), with that day's FX.
- SELL and TRANSFER_OUT consume the oldest lots first.
- TRANSFER_IN opens a lot with NO cost (unevidenced) unless a human approved
  carrying the basis over: then it receives the exact lots the matching
  TRANSFER_OUT took, keeping their original acquisition dates, costs and FX.
- Selling more than the lots hold (a position break) consumes an unevidenced
  shortfall lot rather than failing.

Every lot remembers the transaction that created it, and every lot it passed
through, which is what the drill-down shows.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from pipeline.reconcile import security_key
from pipeline.schema import SourceRef, Transaction, TxnType


@dataclass
class Lot:
    account_id: str
    key: str                       # ISIN, or NAME:<bank name> if unresolved
    acquired: date
    quantity: Decimal
    unit_cost: Decimal | None      # trade currency per unit; None = unevidenced
    currency: str
    fx: Decimal | None             # USD per unit of currency on the acquisition date
    source: SourceRef | None       # the transaction that created the lot
    via: list[str] = field(default_factory=list)  # txn ids it was carried through

    @property
    def cost_usd(self) -> Decimal | None:
        if self.unit_cost is None or self.fx is None:
            return None
        return self.quantity * self.unit_cost * self.fx


@dataclass
class Disposal:
    txn: Transaction
    lots: list[Lot]

    @property
    def proceeds_usd(self) -> Decimal:
        return self.txn.gross_amount * self.txn.fx_to_base

    @property
    def evidenced_cost_usd(self) -> Decimal:
        return sum((l.cost_usd for l in self.lots if l.cost_usd is not None), Decimal(0))

    @property
    def unevidenced_quantity(self) -> Decimal:
        return sum((l.quantity for l in self.lots if l.unit_cost is None), Decimal(0))

    @property
    def gain_usd(self) -> Decimal:
        """Unevidenced lots count at zero cost: the conservative tax treatment."""
        return self.proceeds_usd - self.evidenced_cost_usd


_ORDER = {TxnType.BUY: 0, TxnType.TRANSFER_OUT: 1, TxnType.TRANSFER_IN: 2, TxnType.SELL: 3}


def take(lots: list[Lot], qty: Decimal, txn: Transaction) -> list[Lot]:
    """Remove `qty` from the front of `lots` (FIFO) and return what was taken."""
    taken: list[Lot] = []
    while qty > 0 and lots:
        lot = lots[0]
        n = min(qty, lot.quantity)
        taken.append(replace(lot, quantity=n, via=list(lot.via)))
        lot.quantity -= n
        qty -= n
        if lot.quantity == 0:
            lots.pop(0)
    if qty > 0:  # more left than we hold: record the gap as unevidenced, don't fail
        taken.append(Lot(txn.account_id, security_key(txn), txn.trade_date, qty, None,
                         txn.currency, None, txn.source))
    return taken


def build_lots(txns: list[Transaction], carry: dict[str, str] | None = None
               ) -> tuple[dict[tuple[str, str], list[Lot]], list[Disposal]]:
    """Replay the ledger. `carry` maps receipt txn_id -> delivery txn_id (approved BASIS_MISSING)."""
    carry = carry or {}
    lots: dict[tuple[str, str], list[Lot]] = defaultdict(list)
    delivered: dict[str, list[Lot]] = {}
    disposals: list[Disposal] = []
    moving = sorted((t for t in txns if t.type in _ORDER), key=lambda t: (t.trade_date, _ORDER[t.type], t.txn_id))
    for t in moving:
        k = (t.account_id, security_key(t))
        if t.type is TxnType.BUY:
            lots[k].append(Lot(t.account_id, k[1], t.trade_date, t.quantity, t.gross_amount / t.quantity,
                               t.currency, t.fx_to_base, t.source))
        elif t.type is TxnType.TRANSFER_OUT:
            delivered[t.txn_id] = take(lots[k], t.quantity, t)
        elif t.type is TxnType.TRANSFER_IN:
            from_id = carry.get(t.txn_id)
            if from_id in delivered:
                lots[k].extend(replace(l, account_id=t.account_id, via=[*l.via, from_id, t.txn_id])
                               for l in delivered[from_id])
            else:
                lots[k].append(Lot(t.account_id, k[1], t.trade_date, t.quantity, None, t.currency, None, t.source))
        elif t.type is TxnType.SELL:
            disposals.append(Disposal(t, take(lots[k], t.quantity, t)))
    return dict(lots), disposals