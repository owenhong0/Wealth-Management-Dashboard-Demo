"""Securities master and normalize, checked against ground_truth.json."""
import json
from datetime import date
from decimal import Decimal

import pytest

from config import GENERATED_DIR
from pipeline.normalize import normalize
from pipeline.parse_csv import parse_harbour
from pipeline.parse_swift import parse_straits
from pipeline.schema import SourceRef, Transaction, TxnType
from pipeline.secmaster import SecMaster, resolve_securities

GT = json.loads((GENERATED_DIR / "ground_truth.json").read_text())
BOND = "XS9999LIM016"
MERIDIAN_BOND_NAME = GT["security_names"][BOND]["meridian"]


@pytest.fixture(scope="module")
def event():
    return normalize(resolve_securities(parse_straits(parse_harbour({}))))


@pytest.fixture(scope="module")
def sm():
    return SecMaster.load()


def test_every_harbour_and_straits_record_resolves(event):
    assert event["unresolved_names"] == []
    for r in event["transactions"] + event["snapshots"]:
        assert r.isin or r.type is TxnType.FEE


def test_three_bond_names_resolve_or_get_suggested_to_one_isin(sm):
    names = GT["security_names"][BOND]
    assert sm.resolve(names["harbour"]) == (BOND, "alias")
    assert sm.resolve(names["straits"]) == (BOND, "alias")
    # Meridian's full legal name is the planted NAME_CONFLICT: not resolved automatically...
    assert sm.resolve(MERIDIAN_BOND_NAME) == (None, "unresolved")
    # ...but suggested, on coupon and maturity, for a human to approve.
    s = sm.suggest(MERIDIAN_BOND_NAME)
    assert s.isin == BOND and s.score >= 0.7
    assert any("4.25" in r and "2030" in r for r in s.reasons), s.reasons


def test_suggestion_does_not_confuse_equities(sm):
    assert sm.suggest("ASML Holding N.V.").isin == "NL0010273215"
    assert sm.suggest("Tencent Holdings Limited").isin == "KYG875721634"


def test_approved_alias_resolves(sm):
    approved = SecMaster(sm.securities, {MERIDIAN_BOND_NAME: BOND})
    assert approved.resolve(MERIDIAN_BOND_NAME) == (BOND, "approved")


def test_unresolved_name_is_grouped_with_its_records():
    src = SourceRef(bank="meridian", file="meridian_statement.pdf", page=1, line=12)
    t = Transaction(txn_id="MW-1", source=src, account_id="MW-7731", trade_date=date(2023, 7, 20),
                    security_name=MERIDIAN_BOND_NAME, type=TxnType.BUY, quantity=Decimal(100000),
                    price=Decimal("98.75"), gross_amount=Decimal("98750"), currency="USD")
    e = resolve_securities({"transactions": [t, t.model_copy(update={"txn_id": "MW-2"})]})
    (u,) = e["unresolved_names"]
    assert u["security_name"] == MERIDIAN_BOND_NAME and len(u["records"]) == 2
    assert u["suggestion"].isin == BOND


def test_free_transfers_get_currency_and_fx(event):
    xfers = {t.isin: t for t in event["transactions"] if t.type is TxnType.TRANSFER_IN}
    assert (xfers["KYG875721634"].currency, xfers["KYG875721634"].fx_to_base) == ("HKD", Decimal("0.128"))
    assert (xfers["SG1T75931496"].currency, xfers["SG1T75931496"].fx_to_base) == ("SGD", Decimal("0.75"))
    assert event["count_currency_filled"] == 2


def test_every_record_has_fx(event):
    assert all(t.fx_to_base is not None for t in event["transactions"])
    assert all(s.fx_to_base is not None for s in event["snapshots"])


def test_year_end_usd_values_match_ground_truth(event):
    expected = {(p["account_id"], p["isin"]): Decimal(p["market_value_usd"])
                for p in GT["positions_2025_12_31"]}
    for s in event["snapshots"]:
        if s.as_of_date == date(2025, 12, 31):
            usd = (s.market_value * s.fx_to_base).quantize(Decimal("0.01"))
            assert usd == expected[(s.account_id, s.isin)], (s.account_id, s.isin)


def test_missing_fx_rate_fails_loudly():
    from pipeline.normalize import FxTable
    with pytest.raises(ValueError, match="no FX rate for JPY"):
        FxTable.load().rate("JPY", date(2025, 1, 1))