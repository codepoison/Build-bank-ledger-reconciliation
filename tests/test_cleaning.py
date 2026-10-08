"""Unit tests for CSV loading and source-field standardization."""
import pandas as pd
import pytest

from src.cleaning import (
    clean_bank_data,
    clean_ledger_data,
    normalize_description,
    normalize_reference,
    validate_required_columns,
)


def test_normalize_description_cleans_case_punctuation_and_whitespace():
    assert normalize_description("  ACME/Services,  INC. ") == "acme services inc"
    assert normalize_description("North_star & Sons") == "north star sons"


def test_normalize_description_handles_missing_values():
    assert normalize_description(None) == ""
    assert normalize_description(float("nan")) == ""


def test_normalize_reference_removes_formatting_separators():
    assert normalize_reference(" Ref - 00_42 / A ") == "ref0042a"
    assert normalize_reference(None) == ""


def test_validate_required_columns_reports_missing_fields():
    with pytest.raises(ValueError, match="missing required columns: amount"):
        validate_required_columns(pd.DataFrame({"id": [1]}), ["id", "amount"], "Bank data")


def test_clean_bank_data_preserves_originals_and_adds_standardized_fields():
    raw = pd.DataFrame(
        {
            "bank_transaction_id": ["B-1"],
            "transaction_date": ["2025-02-03 15:45:00"],
            "amount": ["$1,234.567"],
            "description": ["  NORTHSTAR/Office   Supplies! "],
            "reference": [" AB- 12 "],
            "transaction_type": ["debit"],
        }
    )

    cleaned = clean_bank_data(raw)

    assert cleaned.loc[0, "transaction_date"] == "2025-02-03 15:45:00"
    assert cleaned.loc[0, "amount"] == "$1,234.567"
    assert cleaned.loc[0, "description"] == "  NORTHSTAR/Office   Supplies! "
    assert cleaned.loc[0, "normalized_date"] == pd.Timestamp("2025-02-03")
    assert cleaned.loc[0, "normalized_amount"] == 1234.57
    assert cleaned.loc[0, "normalized_description"] == "northstar office supplies"
    assert cleaned.loc[0, "normalized_reference"] == "ab12"
    # Cleaning a DataFrame must not mutate the caller's original records.
    assert list(raw.columns) == [
        "bank_transaction_id", "transaction_date", "amount", "description", "reference", "transaction_type"
    ]


def test_clean_ledger_data_uses_narration_as_description():
    raw = pd.DataFrame(
        {
            "ledger_transaction_id": ["L-1"],
            "ledger_date": ["03/02/2025"],
            "amount": ["(42.105)"],
            "narration": ["  Cloud-HOSTING   invoice #7 "],
            "reference": [" INV. 7 "],
            "account_category": ["Software"],
        }
    )

    cleaned = clean_ledger_data(raw)

    assert cleaned.loc[0, "ledger_date"] == "03/02/2025"
    assert cleaned.loc[0, "narration"] == "  Cloud-HOSTING   invoice #7 "
    assert cleaned.loc[0, "normalized_date"] == pd.Timestamp("2025-03-02")
    assert cleaned.loc[0, "normalized_amount"] == -42.11
    assert cleaned.loc[0, "normalized_description"] == "cloud hosting invoice 7"
    assert cleaned.loc[0, "normalized_reference"] == "inv7"


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [("transaction_date", "not-a-date", "invalid transaction_date"), ("amount", "unknown", "invalid amount")],
)
def test_clean_bank_data_rejects_invalid_required_values(column, value, message):
    raw = pd.DataFrame(
        {
            "bank_transaction_id": ["B-1"],
            "transaction_date": ["2025-01-01"],
            "amount": ["10.00"],
            "description": ["Example"],
            "reference": ["REF-1"],
            "transaction_type": ["debit"],
        }
    )
    raw.loc[0, column] = value
    with pytest.raises(ValueError, match=message):
        clean_bank_data(raw)


def test_clean_bank_data_rejects_missing_schema_column():
    raw = pd.DataFrame({"bank_transaction_id": ["B-1"]})
    with pytest.raises(ValueError, match="Bank data is missing required columns"):
        clean_bank_data(raw)


def test_load_bank_csv_reads_and_cleans_a_csv(tmp_path):
    path = tmp_path / "bank.csv"
    pd.DataFrame(
        {
            "bank_transaction_id": ["B-1"], "transaction_date": ["2025-01-01"],
            "amount": [1.2], "description": ["Coffee"], "reference": ["R-1"],
            "transaction_type": ["debit"],
        }
    ).to_csv(path, index=False)

    cleaned = clean_bank_data(path)

    assert cleaned.loc[0, "normalized_amount"] == 1.2
    assert cleaned.loc[0, "normalized_description"] == "coffee"

