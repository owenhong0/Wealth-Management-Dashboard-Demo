"""Harbour Private Bank: CSV export -> canonical records.

Harbour's conventions:
- dates are DD/MM/YYYY
- transaction codes: B, S, XO (delivered free), XI (received free), DV, CP, FE
- Net Amt is signed (buys and fees negative); we store the gross as positive
- Qty on DV/CP rows is the holding the income was paid on, not a movement
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from config import GENERATED_DIR
from pipeline.schema import PositionSnapshot, SourceRef, Transaction, TxnType

BANK = "harbour"
TXN_FILE = "harbour_transactions.csv"
HOLDINGS_FILE = "harbour_holdings.csv"

CODES = {
    "B": TxnType.BUY,
    "S": TxnType.SELL,
    "XO": TxnType.TRANSFER_OUT,
    "XI": TxnType.TRANSFER_IN,
    "DV": TxnType.DIVIDEND,
    "CP": TxnType.COUPON,
    "FE": TxnType.FEE,
}


REMARK_COUNTERPARTY = re.compile(r"(?:Delivered free to|Received free from)\s+(.+)", re.I)


def _date(s: str) -> date:
    return datetime.strptime(s.strip(), "%d/%m/%Y").date()


def _dec(s: str) -> Decimal | None:
    s = s.strip().replace(",", "")
    return Decimal(s) if s else None


def parse_transactions(path: Path) -> list[Transaction]:
    out: list[Transaction] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row["Txn Code"].strip()
            if code not in CODES:
                raise ValueError(f"{path.name} line {reader.line_num}: unknown Txn Code {code!r}")
            net = _dec(row["Net Amt"])
            out.append(Transaction(
                txn_id=f"HPB-{reader.line_num}",
                source=SourceRef(bank=BANK, file=path.name, line=reader.line_num),
                account_id=row["Acct No"].strip(),
                trade_date=_date(row["Trade Dt"]),
                settle_date=_date(row["Settle Dt"]) if row["Settle Dt"].strip() else None,
                security_name=row["Sec Desc"].strip(),
                isin=row["ISIN"].strip() or None,  # Harbour gives ISINs; secmaster still checks them
                type=CODES[code],
                quantity=_dec(row["Qty"]) or Decimal(0),
                price=_dec(row["Price"]),
                gross_amount=abs(net) if net is not None else None,
                currency=row["Ccy"].strip() or None,
                counterparty=_counterparty(row.get("Remarks", "")),
            ))
    return out


def _counterparty(remarks: str) -> str | None:
    m = REMARK_COUNTERPARTY.search(remarks or "")
    return m.group(1).strip() if m else None


def parse_holdings(path: Path) -> list[PositionSnapshot]:
    out: list[PositionSnapshot] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(PositionSnapshot(
                source=SourceRef(bank=BANK, file=path.name, line=reader.line_num),
                account_id=row["Acct No"].strip(),
                as_of_date=_date(row["As Of"]),
                security_name=row["Sec Desc"].strip(),
                isin=row["ISIN"].strip() or None,
                quantity=_dec(row["Qty"]),
                price=_dec(row["Mkt Px"]),
                market_value=_dec(row["Mkt Val"]),
                currency=row["Ccy"].strip(),
            ))
    return out


def parse_harbour(event: dict[str, Any]) -> dict[str, Any]:
    """Pipeline step."""
    folder = Path(event.get("generated_dir", GENERATED_DIR))
    txns = parse_transactions(folder / TXN_FILE)
    snaps = parse_holdings(folder / HOLDINGS_FILE)
    event.setdefault("transactions", []).extend(txns)
    event.setdefault("snapshots", []).extend(snaps)
    event["count_harbour_transactions"] = len(txns)
    event["count_harbour_snapshots"] = len(snaps)
    return event