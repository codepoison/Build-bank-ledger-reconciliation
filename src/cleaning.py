"""Load and standardize bank and ledger CSV records without losing raw values."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Sequence

import pandas as pd


BANK_REQUIRED_COLUMNS = (
    "bank_transaction_id", "transaction_date", "amount", "description",
    "reference", "transaction_type",
)
LEDGER_REQUIRED_COLUMNS = (
    "ledger_transaction_id", "ledger_date", "amount", "narration",
    "reference", "account_category",
)


def validate_required_columns(
    records: pd.DataFrame,
    required_columns: Sequence[str],
    source_name: str = "input",
) -> None:
    """Raise a useful error when a source lacks fields needed downstream."""
    missing = [column for column in required_columns if column not in records.columns]
    if missing:
        raise ValueError(f"{source_name} is missing required columns: {', '.join(missing)}")


def normalize_description(value: object) -> str:
    """Lowercase text and turn punctuation/whitespace runs into one space.

    Replacing punctuation with spaces avoids merging adjacent words (for example,
    ``"ACME/Services"`` becomes ``"acme services"``).
    """
    if pd.isna(value):
        return ""
    text = str(value).casefold()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_reference(value: object) -> str:
    """Canonicalize a reference by lowercasing and dropping separators.

    Reference punctuation is generally a formatting detail (``AB-12`` and
    ``AB 12`` should compare alike), so unlike descriptions it is removed.
    """
    if pd.isna(value):
        return ""
    return re.sub(r"[\W_]+", "", str(value).casefold(), flags=re.UNICODE)


def _parse_dates(values: pd.Series, source_name: str, field_name: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    invalid = parsed.isna()
    if invalid.any():
        rows = ", ".join(str(index) for index in values.index[invalid][:5])
        raise ValueError(f"{source_name} has missing or invalid {field_name} values at row index(es): {rows}")
    # Normalize timestamps to midnight so comparisons operate on calendar dates.
    return parsed.dt.normalize()


def _parse_amounts(values: pd.Series, source_name: str) -> pd.Series:
    """Parse plain numbers and common CSV currency formatting into cents."""
    text = values.astype("string").str.strip()
    # Parentheses are a common accounting notation for negative values.
    parenthesized = text.str.match(r"^\(.*\)$", na=False)
    text = text.str.replace(r"[,$₹€£]", "", regex=True)
    text.loc[parenthesized] = "-" + text.loc[parenthesized].str.slice(1, -1)

    def to_cents(value: object) -> float:
        if pd.isna(value):
            return float("nan")
        try:
            decimal_value = Decimal(str(value))
            if not decimal_value.is_finite():
                return float("nan")
            rounded = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return float(rounded)
        except (InvalidOperation, ValueError):
            return float("nan")

    # Decimal avoids binary-float tie behavior and applies conventional
    # half-away-from-zero rounding for financial amounts.
    numeric = text.map(to_cents)
    invalid = numeric.isna()
    if invalid.any():
        rows = ", ".join(str(index) for index in values.index[invalid][:5])
        raise ValueError(f"{source_name} has missing or invalid amount values at row index(es): {rows}")
    return numeric


def _read_records(source: pd.DataFrame | str | Path, source_name: str) -> pd.DataFrame:
    """Copy a DataFrame or load a CSV, keeping source values unmodified."""
    if isinstance(source, pd.DataFrame):
        return source.copy(deep=True)
    try:
        return pd.read_csv(source)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise ValueError(f"Could not read {source_name} CSV at {source}: {exc}") from exc


def _standardize(
    records: pd.DataFrame,
    *,
    date_column: str,
    description_column: str,
    source_name: str,
) -> pd.DataFrame:
    """Append common standardized columns while preserving every original one."""
    cleaned = records.copy(deep=True)
    cleaned["normalized_date"] = _parse_dates(cleaned[date_column], source_name, date_column)
    cleaned["normalized_amount"] = _parse_amounts(cleaned["amount"], source_name)
    cleaned["normalized_description"] = cleaned[description_column].map(normalize_description)
    cleaned["normalized_reference"] = cleaned["reference"].map(normalize_reference)
    return cleaned


def clean_bank_data(source: pd.DataFrame | str | Path) -> pd.DataFrame:
    """Load and clean bank rows, retaining the original bank fields verbatim.

    Standardized fields are ``normalized_date``, ``normalized_amount``,
    ``normalized_description``, and ``normalized_reference``.
    """
    records = _read_records(source, "bank")
    validate_required_columns(records, BANK_REQUIRED_COLUMNS, "Bank data")
    return _standardize(
        records,
        date_column="transaction_date",
        description_column="description",
        source_name="Bank data",
    )


def clean_ledger_data(source: pd.DataFrame | str | Path) -> pd.DataFrame:
    """Load and clean ledger rows, retaining the original ledger fields verbatim."""
    records = _read_records(source, "ledger")
    validate_required_columns(records, LEDGER_REQUIRED_COLUMNS, "Ledger data")
    return _standardize(
        records,
        date_column="ledger_date",
        description_column="narration",
        source_name="Ledger data",
    )


def load_bank_csv(path: str | Path) -> pd.DataFrame:
    """Convenience loader for a bank CSV."""
    return clean_bank_data(path)


def load_ledger_csv(path: str | Path) -> pd.DataFrame:
    """Convenience loader for a ledger CSV."""
    return clean_ledger_data(path)

