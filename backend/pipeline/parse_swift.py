"""Straits Custody: simplified SWIFT MT535 (holdings) / MT536 (transactions).

Format notes:
- Messages start with `{1:...}{2:O535...}` and end with `-}`.
- Fields look like `:93B::AGGR//UNIT/1500,` (tag 93B, qualifier AGGR, value).
- `:35B:ISIN XS...` is followed by a free-text description line.
- Numbers use a comma decimal and always contain a comma: `42,5`, `1500,`.
- Quantities are UNIT (shares) or FAMT (face amount, bonds).
- MT536 direction: REDE//RECE or DELI; payment: PAYM//APMT (against payment)
  or FREE (no cash, i.e. a transfer). Free transfers carry no price or currency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from config import GENERATED_DIR
from pipeline.schema import PositionSnapshot, SourceRef, Transaction, TxnType

BANK = "straits"
MT535_FILE = "straits_mt535.txt"
MT536_FILE = "straits_mt536.txt"

FIELD = re.compile(r"^:(?P<tag>\d{2}[A-Z]):(?::(?P<qual>[A-Z0-9]{4})//)?(?P<value>.*)$")
MONEY = re.compile(r"^(?:[A-Z]{4}/)?(?P<ccy>[A-Z]{3})?(?P<num>[\d,]+)$")

TXN_TYPE = {
    ("RECE", "APMT"): TxnType.BUY,
    ("DELI", "APMT"): TxnType.SELL,
    ("RECE", "FREE"): TxnType.TRANSFER_IN,
    ("DELI", "FREE"): TxnType.TRANSFER_OUT,
}


def swift_decimal(s: str) -> Decimal:
    """'42,5' -> 42.5, '1500,' -> 1500"""
    whole, _, frac = s.partition(",")
    return Decimal(f"{whole}.{frac}" if frac else whole)


def swift_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def money(value: str) -> tuple[str | None, Decimal]:
    """'ACTU/HKD68,05' or 'HKD102075,' or 'PRCT/100,2' -> (ccy or None, amount)"""
    m = MONEY.match(value)
    if not m:
        raise ValueError(f"not a SWIFT amount: {value!r}")
    return m["ccy"], swift_decimal(m["num"])


@dataclass
class Block:
    """One :16R:FIN ... :16S:FIN block: the fields it holds and where it starts."""
    line: int
    isin: str | None = None
    description: str = ""
    fields: dict[str, tuple[str, int]] = field(default_factory=dict)  # "93B:AGGR" -> (value, line)


@dataclass
class Message:
    mt: str
    line: int
    header: dict[str, str] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)


def read_messages(path: Path) -> list[Message]:
    messages: list[Message] = []
    msg: Message | None = None
    block: Block | None = None
    expect_description = False
    for n, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("{1:"):
            mt = re.search(r"\{2:O(\d{3})", line)
            msg = Message(mt=mt.group(1) if mt else "?", line=n)
            messages.append(msg)
            continue
        if msg is None:
            raise ValueError(f"{path.name} line {n}: field before message header")
        if line == "-}":
            msg = None
            continue
        if expect_description and not line.startswith(":"):
            block.description = line
            expect_description = False
            continue
        expect_description = False
        m = FIELD.match(line)
        if not m:
            raise ValueError(f"{path.name} line {n}: can't read {line!r}")
        tag, qual, value = m["tag"], m["qual"], m["value"]
        if (tag, value) == ("16R", "FIN"):
            block = Block(line=n)
            msg.blocks.append(block)
        elif (tag, value) == ("16S", "FIN"):
            block = None
        elif tag == "35B" and block is not None:
            block.isin = value.removeprefix("ISIN ").strip() if value.startswith("ISIN ") else None
            block.line = n  # point evidence at the security line
            expect_description = True
        elif block is not None and qual:
            block.fields[f"{tag}:{qual}"] = (value, n)
        elif qual:
            msg.header[f"{tag}:{qual}"] = value
    return messages


def _quantity(value: str) -> Decimal:
    """'UNIT/1500,' or 'FAMT/50000,'"""
    _, _, num = value.partition("/")
    return swift_decimal(num)


def parse_mt535(path: Path) -> list[PositionSnapshot]:
    out: list[PositionSnapshot] = []
    for msg in read_messages(path):
        if msg.mt != "535":
            continue
        as_of = swift_date(msg.header["98A:STAT"])
        account = msg.header["97A:SAFE"]
        for b in msg.blocks:
            qty_value, _ = b.fields["93B:AGGR"]
            price_value = (b.fields.get("90B:MRKT") or b.fields.get("90A:MRKT") or (None,))[0]
            ccy, mv = money(b.fields["19A:HOLD"][0])
            out.append(PositionSnapshot(
                source=SourceRef(bank=BANK, file=path.name, line=b.line),
                account_id=account,
                as_of_date=as_of,
                security_name=b.description,
                isin=b.isin,
                quantity=_quantity(qty_value),
                price=money(price_value)[1] if price_value else None,
                market_value=mv,
                currency=ccy,
            ))
    return out


def parse_mt536(path: Path) -> list[Transaction]:
    out: list[Transaction] = []
    for msg in read_messages(path):
        if msg.mt != "536":
            continue
        account = msg.header["97A:SAFE"]
        for b in msg.blocks:
            f = {k: v for k, (v, _) in b.fields.items()}
            kind = (f["22H:REDE"], f["22H:PAYM"])
            if kind not in TXN_TYPE:
                raise ValueError(f"{path.name} line {b.line}: unknown direction/payment {kind}")
            price_value = f.get("90B:DEAL") or f.get("90A:DEAL")
            amount_ccy, amount = money(f["19A:PSTA"]) if "19A:PSTA" in f else (None, None)
            price_ccy, price = money(price_value) if price_value else (None, None)
            out.append(Transaction(
                txn_id=f"SC-{b.line}",
                source=SourceRef(bank=BANK, file=path.name, line=b.line),
                account_id=account,
                trade_date=swift_date(f["98A:TRAD"]),
                settle_date=swift_date(f["98A:ESET"]) if "98A:ESET" in f else None,
                security_name=b.description,
                isin=b.isin,
                type=TXN_TYPE[kind],
                quantity=_quantity(f["36B:PSTA"]),
                price=price,
                gross_amount=amount,
                currency=amount_ccy or price_ccy,
                counterparty=f.get("95Q:DEAG") or f.get("95Q:REAG"),
            ))
    return out


def parse_straits(event: dict[str, Any]) -> dict[str, Any]:
    """Pipeline step."""
    folder = Path(event.get("generated_dir", GENERATED_DIR))
    txns = parse_mt536(folder / MT536_FILE)
    snaps = parse_mt535(folder / MT535_FILE)
    event.setdefault("transactions", []).extend(txns)
    event.setdefault("snapshots", []).extend(snaps)
    event["count_straits_transactions"] = len(txns)
    event["count_straits_snapshots"] = len(snaps)
    return event