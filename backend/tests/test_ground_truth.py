"""THE CHECKPOINT: the whole pipeline, from the three banks' files, must
reproduce data/generated/ground_truth.json. Exceptions and payoff numbers,
before and after a human approves.
"""
import json
from decimal import Decimal

import pytest

from config import GENERATED_DIR
from pipeline.run import run_steps
from pipeline.schema import ExceptionStatus, ExceptionType

GT = json.loads((GENERATED_DIR / "ground_truth.json").read_text())
BEFORE, AFTER = GT["payoff_before_approval"], GT["payoff_after_basis_approval"]
NUMBERS = ["taxable_account_by_account_usd", "taxable_consolidated_usd", "tax_account_by_account_usd",
           "tax_consolidated_usd", "unevidenced_market_value_usd"]


def approve(*types: ExceptionType) -> dict:
    """Run once, then approve every exception of the given types."""
    ids = [e.exception_id for e in run_steps()["exceptions"] if e.type in types]
    return {i: {"status": "APPROVED", "decided_by": "test", "decided_at": "2026-10-21T00:00:00Z"} for i in ids}


@pytest.fixture(scope="module")
def before():
    return run_steps()


@pytest.fixture(scope="module")
def after_basis():
    return run_steps({"decisions": approve(ExceptionType.BASIS_MISSING)})


def by_type(event):
    return {e.type.value: e for e in event["exceptions"]}


def test_exactly_the_planted_exceptions(before):
    assert sorted(e.type.value for e in before["exceptions"]) == sorted(
        x["type"] for x in GT["expected_exceptions"])


def test_each_exception_matches_ground_truth(before):
    got = by_type(before)
    exp = {x["type"]: x for x in GT["expected_exceptions"]}

    nc = got["NAME_CONFLICT"]
    assert nc.account_id == "MW-7731" and nc.fix["isin"] == exp["NAME_CONFLICT"]["resolves_to"]

    bm = got["BASIS_MISSING"]
    assert (bm.account_id, bm.isin) == ("SC-4420", exp["BASIS_MISSING"]["isin"])
    assert bm.details["delivered_by"] == "HPB-001" and bm.details["quantity"] == "400"
    assert bm.fix["action"] == "carry_basis" and len(bm.evidence) == 2

    ut = got["UNMATCHED_TRANSFER"]
    assert (ut.isin, ut.details["received_from"]) == (exp["UNMATCHED_TRANSFER"]["isin"], "KESTREL SECURITIES SG")
    assert ut.fix is None  # nothing to apply automatically

    pb, pbx = got["POSITION_BREAK"], exp["POSITION_BREAK"]
    assert (pb.isin, pb.details["year"]) == (pbx["isin"], pbx["year"])
    assert [pb.details[k] for k in ("opening", "net_trades", "expected_closing", "statement_closing", "difference")] \
        == [pbx[k] for k in ("opening", "net_trades", "expected_closing", "statement_closing", "difference")]
    assert pb.fix is None  # never plugged


def test_every_exception_cites_real_source_lines(before):
    for e in before["exceptions"]:
        assert e.evidence, e.exception_id
        for ref in e.evidence:
            assert ref.line >= 1 and ref.file in {"harbour_transactions.csv", "meridian_statement.pdf",
                                                  "straits_mt536.txt", "straits_mt535.txt",
                                                  "harbour_holdings.csv"}, ref


@pytest.mark.parametrize("field", NUMBERS)
def test_payoff_before_approval(before, field):
    assert before["payoff"][field] == Decimal(BEFORE[field])


def test_realized_by_account_before(before):
    assert before["payoff"]["realized_gain_by_account_usd"] == {
        k: Decimal(v) for k, v in BEFORE["realized_gain_by_account_usd"].items()}


@pytest.mark.parametrize("field", NUMBERS)
def test_payoff_after_basis_approval(after_basis, field):
    assert after_basis["payoff"][field] == Decimal(AFTER[field])


def test_baseline_stays_put_after_approval(after_basis):
    """The panel shows before vs now: the baseline must not move."""
    for field in NUMBERS:
        assert after_basis["payoff_baseline"][field] == Decimal(BEFORE[field])


def test_approved_status_shows_in_queue(after_basis):
    bm = by_type(after_basis)["BASIS_MISSING"]
    assert bm.status is ExceptionStatus.APPROVED and bm.decided_by == "test"
    assert len(after_basis["exceptions"]) == 4  # approved exceptions stay in the queue


def test_carried_lots_keep_their_harbour_history(after_basis):
    sale = next(d for d in after_basis["payoff"]["disposals"] if d["isin"] == "KYG875721634")
    assert [(l["acquired"], l["quantity"], l["unit_cost"]) for l in sale["lots"]] == [
        ("2023-02-10", Decimal(250), Decimal(380))]
    assert sale["lots"][0]["source"] == "harbour_transactions.csv line 3"  # the original purchase
    assert sale["gain_usd"] == Decimal("4480.00")


def test_overriding_changes_nothing():
    d = {k: {**v, "status": "OVERRIDDEN"} for k, v in approve(ExceptionType.BASIS_MISSING).items()}
    e = run_steps({"decisions": d})
    for field in NUMBERS:
        assert e["payoff"][field] == Decimal(BEFORE[field])


def test_approving_the_name_conflict_merges_the_bond():
    e = run_steps({"decisions": approve(ExceptionType.NAME_CONFLICT)})
    face = sum(s.quantity for s in e["snapshots"]
               if s.isin == "XS9999LIM016" and s.as_of_date.isoformat() == "2025-12-31")
    assert face == Decimal(GT["bond_total_face"])  # 350,000 across three banks
    for field in NUMBERS:  # a naming fix doesn't change any tax number
        assert e["payoff"][field] == Decimal(BEFORE[field])


def test_approving_everything_gives_the_after_numbers():
    e = run_steps({"decisions": approve(*ExceptionType)})
    for field in NUMBERS:
        assert e["payoff"][field] == Decimal(AFTER[field])
    assert len(e["payoff"]["unevidenced_positions"]) == 1  # Singtel: no fix exists, still unevidenced