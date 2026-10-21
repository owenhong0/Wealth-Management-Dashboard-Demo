"""Meridian Wealth: PDF statement -> canonical records, via LLM extraction.

How it works:
1. pdfplumber pulls each page's text; every line gets a label like [p3 L19].
2. The model returns JSON in a strict schema: every row cites the page and
   line holding its numbers, and copies values as printed. It never computes.
3. Deterministic checks verify every row against the page text (see `check`).
   If any check fails, the model gets one retry with the list of problems;
   if it fails again, the run fails. Bad extractions are rejected, never used.
4. A passing result is cached in data/cache/, keyed by the PDF's SHA-256, so
   the deployed app never calls the model. The cache is re-checked on load.

Only CLOSING positions become snapshots; opening positions are used to check
that each page's opening agrees with the previous page's closing.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal

import pdfplumber
from pydantic import BaseModel, ConfigDict, ValidationError

from config import CACHE_DIR, GENERATED_DIR, USE_CACHED_LLM
from pipeline.schema import PositionSnapshot, SourceRef, Transaction, TxnType

BANK = "meridian"
PDF_FILE = "meridian_statement.pdf"
CACHE_FILE = "meridian_extraction.json"
MAX_ATTEMPTS = 2

# --- what the model must return ----------------------------------------------------------


class _X(BaseModel):
    model_config = ConfigDict(extra="forbid")


class XPosition(_X):
    page: int
    line: int                       # the line holding quantity, price and market value
    section: Literal["opening", "closing"]
    as_of_date: date
    security_name: str              # full name, wrapped lines joined with one space
    quantity: Decimal
    price: Decimal | None
    currency: str
    market_value: Decimal


class XTransaction(_X):
    page: int
    line: int                       # the line holding the date and amount
    trade_date: date
    security_name: str
    type: Literal["Purchase", "Sale", "Dividend", "Coupon", "Fee", "Transfer in", "Transfer out"]
    quantity: Decimal | None
    price: Decimal | None
    currency: str
    amount: Decimal                 # signed, as printed


class XStatement(_X):
    account_id: str
    positions: list[XPosition]
    transactions: list[XTransaction]


_NUM = {"type": "string", "description": "Number exactly as printed, without thousands separators, e.g. '-17250.00'"}
_NUM_OR_NULL = {"type": ["string", "null"], "description": "Number as printed without separators, or null if blank"}
JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["account_id", "positions", "transactions"],
    "properties": {
        "account_id": {"type": "string"},
        "positions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["page", "line", "section", "as_of_date", "security_name", "quantity", "price",
                         "currency", "market_value"],
            "properties": {
                "page": {"type": "integer"},
                "line": {"type": "integer", "description": "Line with the numbers"},
                "section": {"type": "string", "enum": ["opening", "closing"]},
                "as_of_date": {"type": "string", "description": "YYYY-MM-DD"},
                "security_name": {"type": "string", "description": "Full name; join wrapped lines"},
                "quantity": _NUM, "price": _NUM_OR_NULL,
                "currency": {"type": "string"}, "market_value": _NUM,
            }}},
        "transactions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["page", "line", "trade_date", "security_name", "type", "quantity", "price",
                         "currency", "amount"],
            "properties": {
                "page": {"type": "integer"},
                "line": {"type": "integer", "description": "Line with the date and amount"},
                "trade_date": {"type": "string", "description": "YYYY-MM-DD"},
                "security_name": {"type": "string", "description": "Full name; join wrapped lines"},
                "type": {"type": "string", "enum": ["Purchase", "Sale", "Dividend", "Coupon", "Fee",
                                                    "Transfer in", "Transfer out"]},
                "quantity": _NUM_OR_NULL, "price": _NUM_OR_NULL,
                "currency": {"type": "string"}, "amount": _NUM,
            }}},
    },
}

SYSTEM = (
    "You extract data from bank statements into JSON. Copy values exactly as printed. "
    "Never calculate, infer, correct or reorder anything: if the statement looks wrong, "
    "copy it as it is. Every row must cite the page and line where its numbers appear."
)

INSTRUCTIONS = """Extract this Meridian Wealth statement.

Rules:
- positions: every row under "Opening positions" (section "opening") and "Closing positions"
  (section "closing"). as_of_date is the date in that section's heading. Skip "No positions held".
- transactions: every row under "Transactions".
- A security name may wrap onto the lines above and below the line with the numbers.
  Join the parts in reading order with single spaces to give the full name.
- "100,000 nominal" means quantity 100000 (a bond's face value).
- Numbers: drop thousands separators, keep the sign and decimals as printed.
- Dates: YYYY-MM-DD.
- Cite the line holding the row's numbers, using the [pN LM] labels.

Statement text:
"""

# --- reading the PDF -----------------------------------------------------------------------


def read_pages(path: Path) -> list[list[str]]:
    with pdfplumber.open(path) as pdf:
        return [(page.extract_text() or "").splitlines() for page in pdf.pages]


def numbered_text(pages: list[list[str]]) -> str:
    return "\n".join(f"[p{p} L{n}] {text}" for p, lines in enumerate(pages, 1)
                     for n, text in enumerate(lines, 1))


# --- deterministic checks ---------------------------------------------------------------------

_TXN_DATE = re.compile(r"\b\d{2} [A-Z][a-z]{2} \d{4}\b")
_ROW_END = re.compile(r"\b[A-Z]{3} -?[\d,]+\.\d{2}$")


def _fmt(x: Decimal, places: int = 2) -> str:
    return f"{abs(x):,.{places}f}"


def _sections(lines: list[str]) -> dict[str, range]:
    """Line ranges (1-based) of the opening, transactions and closing sections on a page."""
    starts = {}
    for n, text in enumerate(lines, 1):
        for key, prefix in (("opening", "Opening positions"), ("transactions", "Transactions "),
                            ("closing", "Closing positions")):
            if text.startswith(prefix):
                starts[key] = n
    order = sorted(starts.items(), key=lambda kv: kv[1])
    out = {}
    for i, (key, start) in enumerate(order):
        end = order[i + 1][1] if i + 1 < len(order) else len(lines) + 1
        out[key] = range(start + 1, end)
    return out


def check(x: XStatement, pages: list[list[str]]) -> list[str]:
    """Every problem found, as a sentence. Empty list = the extraction is accepted."""
    problems: list[str] = []
    all_text = "\n".join("\n".join(p) for p in pages)
    if f"Account: {x.account_id}" not in all_text:
        problems.append(f"account_id {x.account_id!r} does not appear in the statement")

    def line_of(page: int, line: int) -> str | None:
        if 1 <= page <= len(pages) and 1 <= line <= len(pages[page - 1]):
            return pages[page - 1][line - 1]
        return None

    for t in x.transactions:
        where = f"transaction p{t.page} L{t.line}"
        text = line_of(t.page, t.line)
        if text is None:
            problems.append(f"{where}: no such line")
            continue
        for label, needle in (("amount", _fmt(t.amount)), ("date", t.trade_date.strftime("%d %b %Y")),
                              ("currency", t.currency)):
            if needle not in text:
                problems.append(f"{where}: {label} {needle!r} not on the cited line")
        if t.quantity is not None and _fmt(t.quantity, 0) not in text:
            problems.append(f"{where}: quantity {_fmt(t.quantity, 0)!r} not on the cited line")

    for p in x.positions:
        where = f"{p.section} position p{p.page} L{p.line}"
        text = line_of(p.page, p.line)
        if text is None:
            problems.append(f"{where}: no such line")
            continue
        for label, needle in (("market value", _fmt(p.market_value)), ("quantity", _fmt(p.quantity, 0)),
                              ("currency", p.currency)):
            if needle not in text:
                problems.append(f"{where}: {label} {needle!r} not on the cited line")

    # Row counts: catch dropped or invented rows.
    for page_no, lines in enumerate(pages, 1):
        sec = _sections(lines)
        expected = {
            "transactions": sum(1 for n in sec.get("transactions", []) if _TXN_DATE.search(lines[n - 1])),
            "opening": sum(1 for n in sec.get("opening", []) if _ROW_END.search(lines[n - 1])),
            "closing": sum(1 for n in sec.get("closing", []) if _ROW_END.search(lines[n - 1])),
        }
        got = {
            "transactions": sum(1 for t in x.transactions if t.page == page_no),
            "opening": sum(1 for p in x.positions if p.page == page_no and p.section == "opening"),
            "closing": sum(1 for p in x.positions if p.page == page_no and p.section == "closing"),
        }
        for key in expected:
            if expected[key] != got[key]:
                problems.append(f"page {page_no}: {expected[key]} {key} rows on the page, {got[key]} extracted")

    # Each page's opening must equal the previous page's closing.
    closing = defaultdict(dict)
    for p in x.positions:
        if p.section == "closing":
            closing[p.page][p.security_name] = (p.quantity, p.market_value)
    for p in x.positions:
        if p.section == "opening":
            prev = closing.get(p.page - 1, {}).get(p.security_name)
            if prev != (p.quantity, p.market_value):
                problems.append(f"opening position p{p.page} L{p.line} ({p.security_name}) does not match "
                                f"page {p.page - 1}'s closing")
    return problems


# --- extraction, with retry and cache ------------------------------------------------------------

LLM = Callable[..., tuple[dict[str, Any], dict[str, Any]]]


class ExtractionRejected(RuntimeError):
    pass


def extract(pages: list[list[str]], llm: LLM) -> tuple[XStatement, dict[str, Any]]:
    """Ask the model, check the answer, retry once with the problems listed."""
    prompt = INSTRUCTIONS + numbered_text(pages)
    problems: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        user = prompt if not problems else (
            prompt + "\n\nYour previous answer had these problems. Fix them and answer again:\n- "
            + "\n- ".join(problems))
        raw, meta = llm(SYSTEM, user, JSON_SCHEMA, "meridian_statement")
        try:
            x = XStatement.model_validate(raw)
        except ValidationError as exc:
            problems = [f"schema: {e['loc']}: {e['msg']}" for e in exc.errors()][:20]
            continue
        problems = check(x, pages)
        if not problems:
            return x, {**meta, "attempts": attempt}
    raise ExtractionRejected(f"extraction rejected after {MAX_ATTEMPTS} attempts:\n- " + "\n- ".join(problems))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_or_extract(pdf_path: Path, cache_path: Path, llm: LLM | None, use_cache: bool) -> tuple[XStatement, str]:
    """Returns (statement, where it came from: 'cache' or 'llm')."""
    pages = read_pages(pdf_path)
    digest = _sha256(pdf_path)
    if use_cache and cache_path.exists():
        cached = json.loads(cache_path.read_text())
        if cached.get("pdf_sha256") == digest:
            x = XStatement.model_validate(cached["extraction"])
            problems = check(x, pages)  # never trust the cache blindly either
            if problems:
                raise ExtractionRejected(f"cached extraction fails checks; delete {cache_path.name}:\n- "
                                         + "\n- ".join(problems))
            return x, "cache"
    if llm is None:
        from llm import complete_json
        llm = complete_json
    x, meta = extract(pages, llm)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "pdf_file": pdf_path.name,
        "pdf_sha256": digest,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **meta,
        "extraction": x.model_dump(mode="json"),
    }, indent=2) + "\n")
    return x, "llm"


# --- canonical records -----------------------------------------------------------------------------

TYPE = {"Purchase": TxnType.BUY, "Sale": TxnType.SELL, "Dividend": TxnType.DIVIDEND, "Coupon": TxnType.COUPON,
        "Fee": TxnType.FEE, "Transfer in": TxnType.TRANSFER_IN, "Transfer out": TxnType.TRANSFER_OUT}


def to_records(x: XStatement, file: str) -> tuple[list[Transaction], list[PositionSnapshot]]:
    txns = [Transaction(
        txn_id=f"MW-p{t.page}-L{t.line}",
        source=SourceRef(bank=BANK, file=file, page=t.page, line=t.line),
        account_id=x.account_id,
        trade_date=t.trade_date,
        security_name=t.security_name,
        type=TYPE[t.type],
        quantity=t.quantity or Decimal(0),
        price=t.price,
        gross_amount=abs(t.amount),
        currency=t.currency,
    ) for t in x.transactions]
    snaps = [PositionSnapshot(
        source=SourceRef(bank=BANK, file=file, page=p.page, line=p.line),
        account_id=x.account_id,
        as_of_date=p.as_of_date,
        security_name=p.security_name,
        quantity=p.quantity,
        price=p.price,
        market_value=p.market_value,
        currency=p.currency,
    ) for p in x.positions if p.section == "closing"]
    return txns, snaps


def parse_meridian(event: dict[str, Any]) -> dict[str, Any]:
    """Pipeline step. Tests can pass a stand-in model as event["llm"]."""
    folder = Path(event.get("generated_dir", GENERATED_DIR))
    cache_dir = Path(event.get("cache_dir", CACHE_DIR))
    x, origin = load_or_extract(folder / PDF_FILE, cache_dir / CACHE_FILE, event.get("llm"),
                                event.get("use_cached_llm", USE_CACHED_LLM))
    txns, snaps = to_records(x, PDF_FILE)
    event.setdefault("transactions", []).extend(txns)
    event.setdefault("snapshots", []).extend(snaps)
    event["count_meridian_transactions"] = len(txns)
    event["count_meridian_snapshots"] = len(snaps)
    event["count_meridian_source"] = origin
    return event


def main() -> None:
    """python -m pipeline.parse_pdf [--refresh]: run the extraction on its own."""
    import argparse

    parser = argparse.ArgumentParser(description="Extract the Meridian PDF (uses the cache unless --refresh).")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and call the model")
    args = parser.parse_args()
    cache_path = CACHE_DIR / CACHE_FILE
    x, origin = load_or_extract(GENERATED_DIR / PDF_FILE, cache_path, None, use_cache=not args.refresh)
    cached = json.loads(cache_path.read_text())
    print(f"source: {origin}  model: {cached.get('model')}  attempts: {cached.get('attempts')}")
    print(f"usage: {cached.get('usage')}")
    print(f"{len(x.transactions)} transactions, "
          f"{sum(p.section == 'closing' for p in x.positions)} closing positions; every row passed the checks")
    print(f"cache: {cache_path}")


if __name__ == "__main__":
    main()