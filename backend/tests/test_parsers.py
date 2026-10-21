"""The Harbour and Straits parsers read the generated files back correctly,
checked against data/generated/ground_truth.json."""
import json
from datetime import date
from decimal import Decimal

import pytest

from config import GENERATED_DIR
from pipeline.parse_csv import parse_harbour
from pipeline.parse_swift import parse_straits
from pipeline.schema import TxnType

GT = json.loads((GENERATED_DIR / "ground_truth.json").read_text())
BOND = "XS9999LIM016"
TENCENT = "KYG875721634"
SINGTEL = "SG1T75931496"


@pytest.fixture(scope="module")
def parsed():
    return parse_straits(parse_harbour({}))


def by_account(records, account):
    return [r for r in records if r.account_id == account]


def test_transaction_counts_match_ground_truth(parsed):
    assert parsed["count_harbour_transactions"] == GT["counts"]["by_bank"]["harbour"]  # 41
    assert parsed["count_straits_transactions"] == GT["counts"]["by_bank"]["straits"]  # 7


@pytest.mark.parametrize("account", ["HPB-001", "SC-4420"])
def test_year_end_2025_positions_match_ground_truth(parsed, account):
    snaps = {s.isin: s for s in by_account(parsed["snapshots"], account)
             if s.as_of_date == date(2025, 12, 31)}
    expected = {p["isin"]: p for p in GT["positions_2025_12_31"] if p["account_id"] == account}
    assert set(snaps) == set(expected)
    for isin, p in expected.items():
        assert snaps[isin].quantity == Decimal(p["quantity"]), isin
        assert snaps[isin].market_value == Decimal(p["market_value"]), isin
        assert snaps[isin].currency == p["currency"], isin


def test_trade_amounts_reconcile_to_quantity_times_price(parsed):
    """Bonds are priced per 100 face, so the factor matters."""
    for t in parsed["transactions"]:
        if t.type in (TxnType.BUY, TxnType.SELL):
            factor = Decimal("0.01") if t.isin == BOND else Decimal(1)
            assert t.quantity * t.price * factor == t.gross_amount, t.txn_id


def test_tencent_transfer_pair_is_visible_on_both_sides(parsed):
    out = [t for t in parsed["transactions"] if t.type is TxnType.TRANSFER_OUT]
    inn = [t for t in parsed["transactions"] if t.type is TxnType.TRANSFER_IN and t.isin == TENCENT]
    assert len(out) == 1 and len(inn) == 1
    out, inn = out[0], inn[0]
    assert (out.account_id, inn.account_id) == ("HPB-001", "SC-4420")
    assert out.trade_date == inn.trade_date == date(2024, 6, 14)
    assert out.quantity == inn.quantity == 400
    assert inn.cost_basis is None and inn.gross_amount is None   # the planted gap
    assert "STRAITS" in out.counterparty and "HARBOUR" in inn.counterparty


def test_singtel_arrives_from_an_unknown_custodian(parsed):
    (t,) = [t for t in parsed["transactions"] if t.isin == SINGTEL]
    assert t.type is TxnType.TRANSFER_IN and t.quantity == 2000
    assert t.counterparty == "KESTREL SECURITIES SG"


def test_bond_names_differ_by_bank(parsed):
    names = {t.security_name for t in parsed["transactions"] if t.isin == BOND}
    assert names == {GT["security_names"][BOND]["harbour"], GT["security_names"][BOND]["straits"]}


def test_source_lines_point_at_the_record(parsed):
    """Drill-down depends on this: the cited line must contain the security."""
    for t in parsed["transactions"] + parsed["snapshots"]:
        if not t.isin:
            continue
        line = (GENERATED_DIR / t.source.file).read_text().splitlines()[t.source.line - 1]
        assert t.isin in line, (t.source.label(), line)