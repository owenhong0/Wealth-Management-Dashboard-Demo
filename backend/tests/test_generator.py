"""The generator's numbers match the hand calculation in its docstring."""
from synth.generate import build_ledger, check_by_hand, ground_truth


def test_ground_truth_matches_hand_calculation():
    ledger = build_ledger()
    check_by_hand(ground_truth(ledger), ledger)  # raises AssertionError on drift


def test_exactly_four_planted_exceptions():
    types = [e["type"] for e in ground_truth(build_ledger())["expected_exceptions"]]
    assert types == ["NAME_CONFLICT", "BASIS_MISSING", "UNMATCHED_TRANSFER", "POSITION_BREAK"]