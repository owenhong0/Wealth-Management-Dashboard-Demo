"""Normalize: fill in what the banks left out, and convert to the base currency.

- Currency: SWIFT free transfers carry no currency, so take it from the
  securities master.
- FX: `fx_to_base` = USD per unit of the record's currency, from fx_rates.csv,
  using the rate for the record's year (trade date, or statement date).

A missing rate is an error, not a guess: the run fails and says which one.
"""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from config import GENERATED_DIR
from pipeline.schema import BASE_CURRENCY

FX_FILE = "fx_rates.csv"


class FxTable:
    def __init__(self, rates: dict[tuple[str, int], Decimal]):
        self._rates = rates

    @classmethod
    def load(cls, folder: Path = GENERATED_DIR) -> "FxTable":
        rates: dict[tuple[str, int], Decimal] = {}
        with open(folder / FX_FILE, newline="") as f:
            for row in csv.DictReader(f):
                rates[(row["currency"], int(row["year"]))] = Decimal(row["usd_per_unit"])
        return cls(rates)

    def rate(self, currency: str, on: date) -> Decimal:
        if currency == BASE_CURRENCY:
            return Decimal(1)
        try:
            return self._rates[(currency, on.year)]
        except KeyError:
            raise ValueError(f"no FX rate for {currency} in {on.year} (add it to {FX_FILE})") from None


def normalize(event: dict[str, Any]) -> dict[str, Any]:
    """Pipeline step. Runs after resolve_securities."""
    folder = Path(event.get("generated_dir", GENERATED_DIR))
    fx = FxTable.load(folder)
    sm = event["secmaster"]

    txns, filled = [], 0
    for t in event.get("transactions", []):
        currency = t.currency
        if currency is None and t.isin in sm.securities:
            currency = sm.securities[t.isin].currency
            filled += 1
        if currency is None:
            raise ValueError(f"{t.source.label()}: no currency and no known security to take it from")
        txns.append(t.model_copy(update={"currency": currency, "fx_to_base": fx.rate(currency, t.trade_date)}))
    event["transactions"] = txns

    event["snapshots"] = [
        s.model_copy(update={"fx_to_base": fx.rate(s.currency, s.as_of_date)})
        for s in event.get("snapshots", [])
    ]
    event["fx"] = fx
    event["count_currency_filled"] = filled
    return event