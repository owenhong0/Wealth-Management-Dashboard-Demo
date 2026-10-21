"""The API end to end: the demo's click path, checked against ground_truth.json."""
import json
from decimal import Decimal

import pytest

from config import GENERATED_DIR

GT = json.loads((GENERATED_DIR / "ground_truth.json").read_text())
BEFORE, AFTER = GT["payoff_before_approval"], GT["payoff_after_basis_approval"]


def queue(client):
    return {e["type"]: e for e in client.get("/api/exceptions").get_json()["items"]}


def headline(client):
    return client.get("/api/payoff").get_json()


def test_queue_starts_with_four_open_exceptions(client):
    body = client.get("/api/exceptions").get_json()
    assert body["total"] == 4 and body["counts"] == {"OPEN": 4}
    bm = queue(client)["BASIS_MISSING"]
    assert [e["label"] for e in bm["evidence"]] == ["harbour_transactions.csv line 21", "straits_mt536.txt line 66"]


def test_payoff_starts_at_the_before_numbers(client):
    p = headline(client)
    assert p["current"]["taxable_account_by_account_usd"] == BEFORE["taxable_account_by_account_usd"]  # 25242.00
    assert p["current"]["unevidenced_market_value_usd"] == BEFORE["unevidenced_market_value_usd"]      # 17374.00
    assert p["change"]["taxable_consolidated_usd"] == "0.00"


def test_money_is_sent_as_exact_strings(client):
    assert isinstance(headline(client)["current"]["tax_consolidated_usd"], str)


def test_approving_the_basis_transfer_moves_the_numbers(client):
    bm = queue(client)["BASIS_MISSING"]
    resp = client.post(f"/api/exceptions/{bm['exception_id']}/approve", json={"decided_by": "Owen"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["exception"]["status"] == "APPROVED" and body["exception"]["decided_by"] == "Owen"
    cur, base = body["payoff"]["current"], body["payoff"]["baseline"]
    for field in ("taxable_account_by_account_usd", "taxable_consolidated_usd", "unevidenced_market_value_usd"):
        assert cur[field] == AFTER[field]    # 13082.00 / 6402.00 / 6622.00
        assert base[field] == BEFORE[field]  # baseline doesn't move
    assert body["payoff"]["change"]["unevidenced_market_value_usd"] == "-10752.00"
    # and it persists: a fresh GET sees the same state
    assert headline(client)["current"]["taxable_consolidated_usd"] == AFTER["taxable_consolidated_usd"]


def test_override_needs_a_note_and_changes_no_numbers(client):
    bm = queue(client)["BASIS_MISSING"]
    url = f"/api/exceptions/{bm['exception_id']}/override"
    assert client.post(url, json={}).status_code == 400
    resp = client.post(url, json={"note": "Harbour confirms a different lot was delivered"})
    assert resp.status_code == 201 and resp.get_json()["exception"]["status"] == "OVERRIDDEN"
    assert headline(client)["current"]["taxable_consolidated_usd"] == BEFORE["taxable_consolidated_usd"]


def test_latest_decision_wins(client):
    bm = queue(client)["BASIS_MISSING"]["exception_id"]
    client.post(f"/api/exceptions/{bm}/approve", json={})
    client.post(f"/api/exceptions/{bm}/override", json={"note": "changed my mind"})
    assert queue(client)["BASIS_MISSING"]["status"] == "OVERRIDDEN"
    assert client.get("/api/decisions").get_json()["total"] == 2  # both kept in the log


def test_reset_voids_decisions_but_keeps_the_log(client):
    bm = queue(client)["BASIS_MISSING"]["exception_id"]
    client.post(f"/api/exceptions/{bm}/approve", json={})
    resp = client.post("/api/reset")
    assert resp.get_json()["voided"] == 1
    assert headline(client)["current"]["taxable_account_by_account_usd"] == BEFORE["taxable_account_by_account_usd"]
    log = client.get("/api/decisions").get_json()["items"]
    assert len(log) == 1 and log[0]["voided_at"] is not None


def test_unknown_exception_is_404(client):
    assert client.post("/api/exceptions/NOPE/approve", json={}).status_code == 404


def test_read_only_deploy_refuses_writes(app, client):
    app.config["ALLOW_WRITES"] = False
    bm = queue(client)["BASIS_MISSING"]["exception_id"]
    assert client.post(f"/api/exceptions/{bm}/approve", json={}).status_code == 403
    assert client.post("/api/reset").status_code == 403


def bond_rows(client):
    items = client.get("/api/positions").get_json()["items"]
    return [r for r in items if r["isin"] == "XS9999LIM016" or "Pacific Harbour" in r["name"]]


def test_ledger_shows_the_bond_split_until_the_name_is_approved(client):
    rows = bond_rows(client)
    assert sorted(Decimal(r["quantity"]) for r in rows) == [Decimal(100000), Decimal(250000)]
    nc = queue(client)["NAME_CONFLICT"]["exception_id"]
    client.post(f"/api/exceptions/{nc}/approve", json={})
    (row,) = bond_rows(client)
    assert Decimal(row["quantity"]) == Decimal(GT["bond_total_face"])  # 350,000, one security
    assert {h["bank"] for h in row["holdings"]} == {"harbour", "meridian", "straits"}
    assert len({h["name_at_bank"] for h in row["holdings"]}) == 3      # three names, one ISIN


def test_ledger_total_matches_ground_truth(client):
    body = client.get("/api/positions").get_json()
    assert body["as_of"] == "2025-12-31"
    expected = sum(Decimal(p["market_value_usd"]) for p in GT["positions_2025_12_31"])
    assert Decimal(body["total_market_value_usd"]) == expected
    flagged = [(h["account_id"], r["isin"]) for r in body["items"] for h in r["holdings"] if h["basis_unevidenced"]]
    assert sorted(flagged) == [("SC-4420", "KYG875721634"), ("SC-4420", "SG1T75931496")]


def test_drill_down_returns_the_cited_source_line(client):
    body = client.get("/api/sources?file=meridian_statement.pdf&page=3&line=19&context=1").get_json()
    cited = [l for l in body["lines"] if l["cited"]]
    assert len(cited) == 1 and "ASML" in cited[0]["text"] and "Sale" in cited[0]["text"]
    assert len(body["lines"]) == 3


@pytest.mark.parametrize("query", ["file=../../.env&line=1", "file=secmaster.csv", "file=meridian_statement.pdf&line=1"])
def test_drill_down_rejects_bad_requests(client, query):
    assert client.get(f"/api/sources?{query}").status_code in (400, 404)