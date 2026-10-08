"""Create a transaction-level audit trail from cleaned source datasets."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src import config
from src.cleaning import clean_bank_data, clean_ledger_data
from src.matching import DUPLICATE, MATCHED, MISSING, MISMATCHED, match_transactions


EXACT_MATCH = "EXACT_MATCH"
FUZZY_MATCH = "FUZZY_MATCH"
MISMATCH = "MISMATCH"
MISSING_BANK_TRANSACTION = "MISSING_BANK_TRANSACTION"
MISSING_LEDGER_TRANSACTION = "MISSING_LEDGER_TRANSACTION"
AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"


def _validate_identifiers(records: pd.DataFrame, column: str, source: str) -> None:
    """Require stable unique IDs so audit rows can be joined back without loss."""
    if records[column].isna().any():
        raise ValueError(f"{source} contains a missing {column}")
    duplicated = records[column].duplicated(keep=False)
    if duplicated.any():
        ids = ", ".join(str(value) for value in records.loc[duplicated, column].head(5))
        raise ValueError(f"{source} contains duplicate {column} values: {ids}")


def _rule_description(row: pd.Series) -> str:
    """Describe the actual rule and configured threshold behind a decision."""
    method = row["match_method"]
    if method == "stage_1_exact_reference_amount_date":
        return (
            "Stage 1: normalized references are equal; absolute amount difference "
            f"<= {config.MATCH_AMOUNT_TOLERANCE:.2f}; date difference "
            f"<= {config.MATCH_DATE_TOLERANCE_DAYS} day(s)."
        )
    if method == "stage_2_amount_date_fuzzy_description":
        return (
            "Stage 2: absolute amount difference <= "
            f"{config.MATCH_AMOUNT_TOLERANCE:.2f}; date difference <= "
            f"{config.MATCH_DATE_TOLERANCE_DAYS} day(s); RapidFuzz description score "
            f">= {config.FUZZY_MATCH_THRESHOLD:.1f}."
        )
    if method == "stage_3_combined_evidence_score":
        return (
            "Stage 3: amount/date within tolerance and combined evidence confidence "
            f">= {config.COMBINED_MATCH_MIN_CONFIDENCE:.2f}, with description score "
            f">= {config.COMBINED_MATCH_MIN_FUZZY_SCORE:.1f}; configured weights are "
            f"text={config.COMBINED_TEXT_WEIGHT:.2f}, reference={config.COMBINED_REFERENCE_WEIGHT:.2f}, "
            f"amount={config.COMBINED_AMOUNT_WEIGHT:.2f}, date={config.COMBINED_DATE_WEIGHT:.2f}."
        )
    if method == "likely_pair_amount_mismatch":
        return (
            "Exception rule: likely corresponding reference or description found, but "
            f"absolute amount difference exceeds {config.MATCH_AMOUNT_TOLERANCE:.2f}."
        )
    return (
        "No acceptable candidate met the configured evidence rules within "
        f"{config.MATCH_DATE_TOLERANCE_DAYS} day(s)."
    )


def _category(row: pd.Series) -> str:
    """Map low-level matcher status/method to an interviewer-friendly outcome."""
    if row["match_status"] == MATCHED:
        return EXACT_MATCH if row["match_method"] == "stage_1_exact_reference_amount_date" else FUZZY_MATCH
    if row["match_status"] == MISMATCHED:
        return MISMATCH
    if row["match_status"] == MISSING:
        return MISSING_BANK_TRANSACTION if pd.isna(row["bank_transaction_id"]) else MISSING_LEDGER_TRANSACTION
    if row["match_status"] == DUPLICATE:
        reason = str(row["exception_reason"]).casefold()
        if "multiple ledger candidates" in reason:
            return AMBIGUOUS_MATCH
        return "DUPLICATE"
    raise ValueError(f"Unexpected matcher status: {row['match_status']}")


def _attach_source_fields(
    audit: pd.DataFrame,
    source: pd.DataFrame,
    *,
    id_column: str,
    output_id_column: str,
    prefix: str,
) -> pd.DataFrame:
    """Attach all source fields and a one-based CSV data-row number by ID."""
    lookup: dict[Any, tuple[int, dict[str, Any]]] = {}
    for row_number, (_, source_row) in enumerate(source.iterrows(), start=1):
        record_id = source_row[id_column]
        fields = {
            (column if column.startswith(f"{prefix}_") else f"{prefix}_{column}"): value
            for column, value in source_row.items()
            if column != id_column
        }
        lookup[record_id] = (row_number, fields)

    row_numbers: list[int | None] = []
    field_names = _source_field_names(source, id_column, prefix)
    attached_fields: dict[str, list[Any]] = {key: [] for key in field_names}
    for record_id in audit[output_id_column]:
        if pd.isna(record_id) or record_id not in lookup:
            row_numbers.append(None)
            for key in field_names:
                attached_fields[key].append(None)
            continue
        number, values = lookup[record_id]
        row_numbers.append(number)
        for key in field_names:
            attached_fields[key].append(values.get(key))

    audit[f"{prefix}_source_row"] = row_numbers
    for key, values in attached_fields.items():
        audit[key] = values
    return audit


def _source_field_names(source: pd.DataFrame, id_column: str, prefix: str) -> list[str]:
    return [
        column if column.startswith(f"{prefix}_") else f"{prefix}_{column}"
        for column in source.columns
        if column != id_column
    ]


def _attach_all_source_fields(audit: pd.DataFrame, bank: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    """Attach bank and ledger source payloads in a consistent, null-safe way."""
    # Rows with an absent source still receive nulls in every field for that side.
    audit = _attach_source_fields(
        audit, bank, id_column="bank_transaction_id",
        output_id_column="bank_transaction_id", prefix="bank",
    )
    audit = _attach_source_fields(
        audit, ledger, id_column="ledger_transaction_id",
        output_id_column="ledger_transaction_id", prefix="ledger",
    )
    return audit


def reconcile_transactions(
    bank_source: pd.DataFrame | str | Path,
    ledger_source: pd.DataFrame | str | Path,
) -> pd.DataFrame:
    """Load/clean both sources, call the matcher, and return a full audit trail.

    Inputs may be CSV paths or DataFrames. Original source columns are carried
    through with ``bank_``/``ledger_`` prefixes, alongside standardized values.
    Every source transaction ID is checked for representation in the result.
    """
    bank = clean_bank_data(bank_source)
    ledger = clean_ledger_data(ledger_source)
    return reconcile_cleaned_data(bank, ledger)


def reconcile_cleaned_data(bank: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    """Reconcile already-cleaned DataFrames and retain their source fields."""
    bank = bank.copy(deep=True)
    ledger = ledger.copy(deep=True)
    _validate_identifiers(bank, "bank_transaction_id", "Bank data")
    _validate_identifiers(ledger, "ledger_transaction_id", "Ledger data")

    matches = match_transactions(bank, ledger)
    matches["reconciliation_type"] = [_category(row) for _, row in matches.iterrows()]
    matches["rule_threshold"] = [_rule_description(row) for _, row in matches.iterrows()]
    matches["decision_explanation"] = [
        row["exception_reason"] if row["exception_reason"] else (
            f"Accepted by {row['match_method']}; candidate score={row['candidate_score']:.2f}, "
            f"description similarity={row['fuzzy_score']:.2f}, "
            f"amount difference={row['amount_difference']:.2f}, "
            f"date difference={row['date_difference']} day(s)."
        )
        for _, row in matches.iterrows()
    ]
    audit = _attach_all_source_fields(matches, bank, ledger)

    represented_bank = set(audit["bank_transaction_id"].dropna())
    represented_ledger = set(audit["ledger_transaction_id"].dropna())
    missing_bank_ids = set(bank["bank_transaction_id"]) - represented_bank
    missing_ledger_ids = set(ledger["ledger_transaction_id"]) - represented_ledger
    if missing_bank_ids or missing_ledger_ids:
        raise RuntimeError(
            "Reconciliation audit omitted source transaction(s): "
            f"bank={sorted(missing_bank_ids)}, ledger={sorted(missing_ledger_ids)}"
        )
    return audit


def reconcile_files(
    bank_path: str | Path = config.BANK_CSV,
    ledger_path: str | Path = config.LEDGER_CSV,
) -> pd.DataFrame:
    """Convenience wrapper for the configured generated CSV paths."""
    return reconcile_transactions(bank_path, ledger_path)

