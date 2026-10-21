"""Canonical records. Every parser emits these; everything downstream reads them.

Rules:
- Every record points back to where it came from (file, page, line), so the
  UI can show the exact source behind any number and every exception.
- Quantities are units for equities and FACE VALUE for bonds. Bond prices are
  quoted per 100 of face, so gross = quantity * price / 100 (see `price_factor`).
- Amounts are in the record's own currency. `fx_to_base` converts to USD and is
  filled in by normalize, not by the parsers.
- `isin` is None until the securities master resolves `security_name`.
"""
from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

BASE_CURRENCY = "USD"


class AssetClass(str, enum.Enum):
    EQUITY = "EQUITY"
    BOND = "BOND"


class TxnType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    DIVIDEND = "DIVIDEND"
    COUPON = "COUPON"
    FEE = "FEE"
    CORP_ACTION = "CORP_ACTION"


# Types that change the number of units held, and in which direction.
QTY_SIGN: dict[TxnType, int] = {
    TxnType.BUY: 1,
    TxnType.TRANSFER_IN: 1,
    TxnType.SELL: -1,
    TxnType.TRANSFER_OUT: -1,
}


class ExceptionType(str, enum.Enum):
    BASIS_MISSING = "BASIS_MISSING"            # units arrived with no cost
    POSITION_BREAK = "POSITION_BREAK"          # opening + trades != closing
    UNMATCHED_TRANSFER = "UNMATCHED_TRANSFER"  # transfer-in with no transfer-out anywhere
    NAME_CONFLICT = "NAME_CONFLICT"            # a bank's security name we can't map to an ISIN


class ExceptionStatus(str, enum.Enum):
    OPEN = "OPEN"
    APPROVED = "APPROVED"
    OVERRIDDEN = "OVERRIDDEN"


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, use_enum_values=False)


class SourceRef(_Record):
    """Where a record came from. `line` is 1-based: the CSV row (header = 1),
    the line in the SWIFT file, or the line in a PDF page's extracted text."""

    bank: str
    file: str
    line: int
    page: int | None = None

    def label(self) -> str:
        where = f"p{self.page} " if self.page else ""
        return f"{self.file} {where}line {self.line}"


class Security(_Record):
    isin: str = Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    name: str
    asset_class: AssetClass
    currency: str
    aliases: tuple[str, ...] = ()

    @property
    def price_factor(self) -> Decimal:
        """Multiply quantity * price by this to get the amount."""
        return Decimal("0.01") if self.asset_class is AssetClass.BOND else Decimal(1)


class Transaction(_Record):
    txn_id: str
    source: SourceRef
    account_id: str
    trade_date: date
    settle_date: date | None = None
    security_name: str            # exactly as the bank wrote it
    isin: str | None = None       # set by the securities master
    type: TxnType
    quantity: Decimal = Decimal(0)        # units or face; 0 for cash-only rows
    price: Decimal | None = None
    gross_amount: Decimal | None = None   # in `currency`; None for free transfers
    currency: str | None = None           # None when the source doesn't say (SWIFT free
                                          # transfers); normalize fills it from the secmaster
    counterparty: str | None = None       # transfers: the other custodian, as the bank wrote it
    fx_to_base: Decimal | None = None     # set by normalize
    cost_basis: Decimal | None = None     # in `currency`; None = not evidenced


class PositionSnapshot(_Record):
    source: SourceRef
    account_id: str
    as_of_date: date
    security_name: str
    isin: str | None = None
    quantity: Decimal
    price: Decimal | None = None
    market_value: Decimal | None = None   # in `currency`
    currency: str
    fx_to_base: Decimal | None = None     # set by normalize


class ReconException(BaseModel):
    """Not frozen: status and decision fields change when a human decides.

    `fix` is the machine-readable action applied if a human approves, e.g.
    {"action": "carry_basis", ...}. None means there is nothing safe to apply
    automatically: approving only records that a person reviewed it.
    """

    exception_id: str
    type: ExceptionType
    account_id: str
    isin: str | None = None
    summary: str
    details: dict[str, Any] = {}
    affected_txn_ids: list[str] = []
    evidence: list[SourceRef] = []
    proposed_fix: str | None = None
    fix: dict[str, Any] | None = None
    confidence: float | None = None
    status: ExceptionStatus = ExceptionStatus.OPEN
    decided_by: str | None = None
    decided_at: str | None = None