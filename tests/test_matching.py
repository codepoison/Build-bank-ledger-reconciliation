"""Behavior tests for staged bank-to-ledger matching."""
import pandas as pd

from src.matching import DUPLICATE, MATCHED, MISSING, MISMATCHED, match_transactions


def bank_row(txn_id="B1", day="2025-01-10", amount=125.50, description="Northstar Office Supplies", reference="REF-100"):
    return {
        "bank_transaction_id": txn_id,
        "transaction_date": day,
        "amount": amount,
        "description": description,
        "reference": reference,
        "transaction_type": "debit",
    }


def ledger_row(txn_id="L1", day="2025-01-10", amount=125.50, narration="Northstar Office Supplies", reference="REF-100"):
    return {
        "ledger_transaction_id": txn_id,
        "ledger_date": day,
        "amount": amount,
        "narration": narration,
        "reference": reference,
        "account_category": "Office Supplies",
    }


def reconcile(banks, ledgers):
    return match_transactions(pd.DataFrame(banks), pd.DataFrame(ledgers))


def test_exact_reference_amount_and_date_match_uses_stage_one():
    result = reconcile([bank_row()], [ledger_row()])

    assert len(result) == 1
    assert result.loc[0, "match_status"] == MATCHED
    assert result.loc[0, "match_method"] == "stage_1_exact_reference_amount_date"
    assert result.loc[0, "bank_transaction_id"] == "B1"
    assert result.loc[0, "ledger_transaction_id"] == "L1"
    assert result.loc[0, "candidate_score"] >= result.loc[0, "fuzzy_score"]


def test_date_difference_at_configured_tolerance_is_accepted():
    result = reconcile(
        [bank_row(day="2025-01-10")],
        [ledger_row(day="2025-01-13")],
    )

    assert result.loc[0, "match_status"] == MATCHED
    assert result.loc[0, "date_difference"] == 3


def test_amount_difference_at_configured_tolerance_is_accepted():
    result = reconcile(
        [bank_row(amount=125.50)],
        [ledger_row(amount=125.51)],
    )

    assert result.loc[0, "match_status"] == MATCHED
    assert result.loc[0, "amount_difference"] == 0.01


def test_fuzzy_description_match_uses_stage_two():
    result = reconcile(
        [bank_row(description="Harbor Freight Services", reference="BANK-REF")],
        [ledger_row(narration="Harbor Freight Service", reference="LEDGER-REF")],
    )

    assert result.loc[0, "match_status"] == MATCHED
    assert result.loc[0, "match_method"] == "stage_2_amount_date_fuzzy_description"
    assert result.loc[0, "fuzzy_score"] >= 88


def test_likely_pair_with_amount_difference_is_mismatched():
    result = reconcile([bank_row()], [ledger_row(amount=145.50)])

    assert result.loc[0, "match_status"] == MISMATCHED
    assert result.loc[0, "ledger_transaction_id"] == "L1"
    assert result.loc[0, "amount_difference"] == 20.0
    assert "amount differs" in result.loc[0, "exception_reason"]


def test_unpaired_rows_are_reported_as_missing_on_both_sides():
    result = reconcile(
        [bank_row(day="2025-01-10")],
        [ledger_row(txn_id="L-OLD", day="2024-12-01")],
    )

    assert set(result["match_status"]) == {MISSING}
    assert len(result) == 2
    assert result["bank_transaction_id"].notna().sum() == 1
    assert result["ledger_transaction_id"].notna().sum() == 1
    assert result["exception_reason"].str.len().gt(0).all()


def test_duplicate_bank_rows_claiming_one_ledger_row_are_not_auto_resolved():
    result = reconcile(
        [bank_row("B1"), bank_row("B2")],
        [ledger_row()],
    )

    assert set(result["match_status"]) == {DUPLICATE}
    assert set(result["bank_transaction_id"]) == {"B1", "B2"}
    assert set(result["ledger_transaction_id"]) == {"L1"}
    assert result["exception_reason"].str.contains("Multiple bank rows").all()


def test_multiple_plausible_ledger_candidates_are_reported_as_ambiguous_duplicates():
    result = reconcile(
        [bank_row()],
        [ledger_row("L1"), ledger_row("L2")],
    )

    assert len(result) == 2
    assert set(result["match_status"]) == {DUPLICATE}
    assert set(result["ledger_transaction_id"]) == {"L1", "L2"}
    assert result["exception_reason"].str.contains("Multiple ledger candidates").all()


def test_output_includes_explainability_fields():
    result = reconcile([bank_row()], [ledger_row()])

    expected = {
        "bank_transaction_id", "ledger_transaction_id", "match_status", "match_method",
        "fuzzy_score", "candidate_score", "date_difference", "amount_difference",
        "confidence", "exception_reason",
    }
    assert expected.issubset(result.columns)

