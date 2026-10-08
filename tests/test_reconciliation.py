"""Tests for transaction-level audit construction and classification labels."""
import pandas as pd

from src.reconciliation import (
    EXACT_MATCH,
    MISSING_BANK_TRANSACTION,
    MISSING_LEDGER_TRANSACTION,
    reconcile_transactions,
)


def test_reconciliation_retains_original_source_fields_and_accounts_for_all_ids():
    bank = pd.DataFrame(
        [
            {"bank_transaction_id": "B1", "transaction_date": "2025-01-10", "amount": 100,
             "description": "  ACME Payment ", "reference": "R1", "transaction_type": "debit"},
            {"bank_transaction_id": "B2", "transaction_date": "2025-02-10", "amount": 50,
             "description": "Bank only", "reference": "R2", "transaction_type": "debit"},
        ]
    )
    ledger = pd.DataFrame(
        [
            {"ledger_transaction_id": "L1", "ledger_date": "2025-01-10", "amount": 100,
             "narration": "ACME Payment", "reference": "R1", "account_category": "Sales"},
            {"ledger_transaction_id": "L2", "ledger_date": "2025-04-10", "amount": 25,
             "narration": "Ledger only", "reference": "R3", "account_category": "Other"},
        ]
    )

    result = reconcile_transactions(bank, ledger)

    by_type = result.set_index("reconciliation_type")
    assert EXACT_MATCH in by_type.index
    assert MISSING_LEDGER_TRANSACTION in by_type.index
    assert MISSING_BANK_TRANSACTION in by_type.index
    assert by_type.loc[EXACT_MATCH, "bank_description"] == "  ACME Payment "
    assert by_type.loc[EXACT_MATCH, "ledger_narration"] == "ACME Payment"
    assert set(result["bank_transaction_id"].dropna()) == {"B1", "B2"}
    assert set(result["ledger_transaction_id"].dropna()) == {"L1", "L2"}
    assert result.loc[result["match_status"] != "MATCHED", "exception_reason"].str.len().gt(0).all()

