"""Meridian PDF parser, tested with a stand-in model.

The stand-in builds its answer from the generator's own ledger (not from the
PDF text), so these tests check that the parser accepts a correct extraction,
rejects wrong ones, retries, caches, and produces the right canonical records.
A real model run is checked by test_real_cache_matches_ground_truth once
data/cache/meridian_extraction.json exists.
"""
import json
from datetime import date
from decimal import Decimal

import pytest

from config import CACHE_DIR, GENERATED_DIR
from pipeline.parse_pdf import (CACHE_FILE, PDF_FILE, ExtractionRejected, XStatement, check,
                                load_or_extract, parse_meridian, read_pages)
from pipeline.schema import TxnType
from synth.generate import MERIDIAN, MERIDIAN_LABELS, SEC, YEAR_END_PRICE, build_ledger, holdings, year_end

GT = json.loads((GENERATED_DIR / "ground_truth.json").read_text())
PAGES = read_pages(GENERATED_DIR / PDF_FILE)
YEARS = (2023, 2024, 2025)


def _find(page: int, *needles: str, after: str | None = None) -> int:
    lines = PAGES[page - 1]
    start = next(i for i, t in enumerate(lines) if t.startswith(after)) if after else 0
    for i in range(start, len(lines)):
        if all(n in lines[i] for n in needles):
            return i + 1
    raise AssertionError(f"p{page}: no line with {needles}")


def correct_answer() -> dict:
    """What a perfect model would return, built from the true ledger."""
    ledger = build_ledger()
    positions, transactions = [], []
    for page, y in enumerate(YEARS, 1):
        for section, as_of, heading in (("opening", year_end(y - 1), "Opening positions"),
                                        ("closing", year_end(y), "Closing positions")):
            for isin, qty in holdings(ledger, MERIDIAN, as_of).items():
                s = SEC[isin]
                px = YEAR_END_PRICE[isin][as_of.year]
                mv = qty * px * s.factor
                positions.append({
                    "page": page, "line": _find(page, f"{mv:,.2f}", after=heading), "section": section,
                    "as_of_date": as_of.isoformat(), "security_name": s.names[MERIDIAN],
                    "quantity": str(qty), "price": f"{px:.2f}", "currency": s.currency,
                    "market_value": f"{mv:.2f}"})
        for t in ledger:
            if t.bank != MERIDIAN or t.hidden or t.trade_date.year != y:
                continue
            sign = -1 if t.type in ("BUY", "FEE") else 1
            transactions.append({
                "page": page, "line": _find(page, t.trade_date.strftime("%d %b %Y"), f"{t.amount:,.2f}"),
                "trade_date": t.trade_date.isoformat(),
                "security_name": SEC[t.isin].names[MERIDIAN] if t.isin else "Management fee",
                "type": MERIDIAN_LABELS[t.type], "quantity": str(t.quantity) if t.quantity else None,
                "price": f"{t.price:.2f}" if t.price is not None else None,
                "currency": t.currency, "amount": f"{sign * t.amount:.2f}"})
    return {"account_id": "MW-7731", "positions": positions, "transactions": transactions}


class FakeLLM:
    """Returns the queued answers in order and records every prompt."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, system, user, schema, name):
        self.prompts.append(user)
        return self.answers.pop(0), {"model": "fake", "usage": {}}


def without_asml_sale(answer: dict) -> dict:
    a = json.loads(json.dumps(answer))
    a["transactions"] = [t for t in a["transactions"] if not (t["type"] == "Sale")]
    return a


def test_correct_answer_passes_every_check():
    assert check(XStatement.model_validate(correct_answer()), PAGES) == []


def test_dropped_row_is_caught():
    problems = check(XStatement.model_validate(without_asml_sale(correct_answer())), PAGES)
    assert any("page 3: 5 transactions rows on the page, 4 extracted" in p for p in problems), problems


def test_wrong_number_is_caught():
    a = correct_answer()
    a["transactions"][0]["amount"] = "-17520.00"  # digits swapped
    problems = check(XStatement.model_validate(a), PAGES)
    assert any("amount '17,520.00' not on the cited line" in p for p in problems), problems


def test_retry_sends_problems_back_and_succeeds(tmp_path):
    fake = FakeLLM(without_asml_sale(correct_answer()), correct_answer())
    x, origin = load_or_extract(GENERATED_DIR / PDF_FILE, tmp_path / CACHE_FILE, fake, use_cache=True)
    assert origin == "llm" and len(fake.prompts) == 2
    assert "previous answer had these problems" in fake.prompts[1]
    assert json.loads((tmp_path / CACHE_FILE).read_text())["attempts"] == 2


def test_rejected_after_two_bad_answers(tmp_path):
    bad = without_asml_sale(correct_answer())
    with pytest.raises(ExtractionRejected, match="rejected after 2 attempts"):
        load_or_extract(GENERATED_DIR / PDF_FILE, tmp_path / CACHE_FILE, FakeLLM(bad, bad), use_cache=True)
    assert not (tmp_path / CACHE_FILE).exists()  # nothing bad gets cached


def test_cache_is_used_and_model_not_called(tmp_path):
    load_or_extract(GENERATED_DIR / PDF_FILE, tmp_path / CACHE_FILE, FakeLLM(correct_answer()), use_cache=True)
    _, origin = load_or_extract(GENERATED_DIR / PDF_FILE, tmp_path / CACHE_FILE, FakeLLM(), use_cache=True)
    assert origin == "cache"


def test_stale_cache_is_ignored(tmp_path):
    load_or_extract(GENERATED_DIR / PDF_FILE, tmp_path / CACHE_FILE, FakeLLM(correct_answer()), use_cache=True)
    cached = json.loads((tmp_path / CACHE_FILE).read_text())
    cached["pdf_sha256"] = "0" * 64  # as if the PDF had been regenerated
    (tmp_path / CACHE_FILE).write_text(json.dumps(cached))
    _, origin = load_or_extract(GENERATED_DIR / PDF_FILE, tmp_path / CACHE_FILE,
                                FakeLLM(correct_answer()), use_cache=True)
    assert origin == "llm"


def _records(tmp_path, answer):
    return parse_meridian({"llm": FakeLLM(answer), "cache_dir": tmp_path})


def test_records_match_ground_truth(tmp_path):
    e = _records(tmp_path, correct_answer())
    assert e["count_meridian_transactions"] == GT["counts"]["by_bank"]["meridian"]  # 15
    closing_2025 = {s.security_name: s for s in e["snapshots"] if s.as_of_date == date(2025, 12, 31)}
    expected = {p["isin"]: p for p in GT["positions_2025_12_31"] if p["account_id"] == "MW-7731"}
    assert len(closing_2025) == len(expected) == 3
    for isin, p in expected.items():
        snap = closing_2025[GT["security_names"][isin]["meridian"]]
        assert snap.quantity == Decimal(p["quantity"]) and snap.market_value == Decimal(p["market_value"])
    sale = next(t for t in e["transactions"] if t.type is TxnType.SELL)
    assert (sale.source.page, sale.source.label()) == (3, "meridian_statement.pdf p3 line 19")


def test_meridian_bond_name_becomes_the_name_conflict(tmp_path):
    from pipeline.secmaster import resolve_securities
    e = resolve_securities(_records(tmp_path, correct_answer()))
    (u,) = e["unresolved_names"]
    assert u["security_name"] == GT["security_names"]["XS9999LIM016"]["meridian"]
    assert u["suggestion"].isin == "XS9999LIM016"
    assert len(u["records"]) == 9  # 1 purchase + 5 coupons + 3 closing positions


@pytest.mark.skipif(not (CACHE_DIR / CACHE_FILE).exists(), reason="no real model run cached yet")
def test_real_cache_matches_ground_truth():
    """Runs once you've done a real extraction: the model's answer, checked like any other."""
    e = parse_meridian({"use_cached_llm": True})
    assert e["count_meridian_source"] == "cache"
    assert e["count_meridian_transactions"] == GT["counts"]["by_bank"]["meridian"]