"""Write reconciliation, discrepancy, summary, and HTML audit reports."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src import config
from src.reconciliation import (
    AMBIGUOUS_MATCH,
    EXACT_MATCH,
    FUZZY_MATCH,
    MISSING_BANK_TRANSACTION,
    MISSING_LEDGER_TRANSACTION,
    MISMATCH,
)


_EXCEPTION_TYPES = {
    MISMATCH,
    MISSING_BANK_TRANSACTION,
    MISSING_LEDGER_TRANSACTION,
    "DUPLICATE",
    AMBIGUOUS_MATCH,
}


def _transaction_count(records: pd.DataFrame, id_column: str) -> int:
    if id_column not in records:
        return 0
    return int(records[id_column].dropna().nunique())


def _distinct_count(records: pd.DataFrame, outcome: str, id_column: str) -> int:
    rows = records[records["reconciliation_type"] == outcome]
    return _transaction_count(rows, id_column)


def _exception_record_count(records: pd.DataFrame) -> int:
    """Count unique source transactions touched by an exception, once per side."""
    exceptions = records[records["reconciliation_type"].isin(_EXCEPTION_TYPES)]
    identifiers: set[tuple[str, Any]] = set()
    for side, column in (("bank", "bank_transaction_id"), ("ledger", "ledger_transaction_id")):
        if column not in exceptions:
            continue
        identifiers.update((side, value) for value in exceptions[column].dropna().unique())
    return len(identifiers)


def _reason(row: pd.Series) -> str:
    """Render a concise reviewer-facing exception reason for the discrepancy CSV."""
    outcome = row["reconciliation_type"]
    if outcome == MISMATCH:
        difference = row.get("amount_difference")
        if pd.notna(difference):
            return f"Likely corresponding ledger transaction found, but amount differs by ₹{float(difference):,.2f}."
        return "Likely corresponding ledger transaction found, but a key field is outside tolerance."
    if outcome == MISSING_LEDGER_TRANSACTION:
        return "No acceptable ledger transaction found within the configured date and amount tolerances."
    if outcome == MISSING_BANK_TRANSACTION:
        return "No acceptable bank transaction found for this ledger entry within the configured date and amount tolerances."
    if outcome == AMBIGUOUS_MATCH:
        return "Multiple ledger candidates exceeded the fuzzy-match or combined-evidence threshold; manual review is required."
    if outcome == "DUPLICATE":
        source_reason = str(row.get("exception_reason", "")).casefold()
        if "multiple bank rows" in source_reason:
            return "Duplicate bank transactions claim the same ledger transaction; manual review is required."
        return "Duplicate ledger transaction detected for the same reference and amount; manual review is required."
    return str(row.get("exception_reason", "Review this reconciliation exception."))


def _build_discrepancy_report(audit: pd.DataFrame) -> pd.DataFrame:
    discrepancies = audit[audit["reconciliation_type"].isin(_EXCEPTION_TYPES)].copy()
    if discrepancies.empty:
        columns = [
            "reconciliation_type", "bank_transaction_id", "ledger_transaction_id",
            "bank_description", "ledger_narration", "bank_normalized_amount",
            "ledger_normalized_amount", "amount_difference", "date_difference",
            "fuzzy_score", "match_method", "rule_threshold", "exception_reason",
        ]
        return pd.DataFrame(columns=columns)

    discrepancies["exception_reason"] = discrepancies.apply(_reason, axis=1)
    preferred = [
        "reconciliation_type", "bank_transaction_id", "ledger_transaction_id",
        "bank_source_row", "ledger_source_row", "bank_description", "ledger_narration",
        "bank_reference", "ledger_reference", "bank_normalized_amount",
        "ledger_normalized_amount", "amount_difference", "date_difference",
        "fuzzy_score", "candidate_score", "confidence", "match_method",
        "rule_threshold", "exception_reason",
    ]
    columns = [column for column in preferred if column in discrepancies.columns]
    return discrepancies[columns].reset_index(drop=True)


def _decision_text(row: pd.Series) -> str:
    """Phrase the evidence behind successful and exceptional decisions."""
    outcome = row["reconciliation_type"]
    if outcome in _EXCEPTION_TYPES:
        return _reason(row)
    score = row.get("fuzzy_score")
    score_text = f"{float(score):.1f}%" if pd.notna(score) else "not available"
    date_diff = row.get("date_difference")
    date_text = f"{int(date_diff)} day(s)" if pd.notna(date_diff) else "not available"
    if outcome == EXACT_MATCH:
        return f"Reference matched and amount matched; description similarity {score_text}; date difference {date_text}."
    if outcome == FUZZY_MATCH:
        return f"Amount matched and description similarity {score_text} met the selected rule; date difference {date_text}."
    return str(row.get("decision_explanation", "Decision recorded by the reconciliation engine."))


def build_summary(audit: pd.DataFrame) -> pd.DataFrame:
    """Calculate actual counts and rates from one transaction-level audit."""
    bank_total = _transaction_count(audit, "bank_transaction_id")
    ledger_total = _transaction_count(audit, "ledger_transaction_id")
    exact_count = _distinct_count(audit, EXACT_MATCH, "bank_transaction_id")
    fuzzy_count = _distinct_count(audit, FUZZY_MATCH, "bank_transaction_id")
    matched_count = exact_count + fuzzy_count
    mismatched_count = _distinct_count(audit, MISMATCH, "bank_transaction_id")
    missing_bank_count = _distinct_count(audit, MISSING_BANK_TRANSACTION, "ledger_transaction_id")
    missing_ledger_count = _distinct_count(audit, MISSING_LEDGER_TRANSACTION, "bank_transaction_id")
    missing_count = missing_bank_count + missing_ledger_count
    duplicate_count = _distinct_count(audit, "DUPLICATE", "bank_transaction_id") + _distinct_count(
        audit, "DUPLICATE", "ledger_transaction_id"
    )
    ambiguous_count = _distinct_count(audit, AMBIGUOUS_MATCH, "bank_transaction_id") + _distinct_count(
        audit, AMBIGUOUS_MATCH, "ledger_transaction_id"
    )

    fuzzy_rows = audit[audit["reconciliation_type"] == FUZZY_MATCH]
    avg_fuzzy = float(fuzzy_rows["fuzzy_score"].dropna().mean()) if "fuzzy_score" in fuzzy_rows else float("nan")

    unmatched_amount = 0.0
    if "bank_normalized_amount" in audit:
        bank_only = audit[audit["reconciliation_type"] == MISSING_LEDGER_TRANSACTION]
        unmatched_amount += float(pd.to_numeric(bank_only["bank_normalized_amount"], errors="coerce").abs().sum())
    if "ledger_normalized_amount" in audit:
        ledger_only = audit[audit["reconciliation_type"] == MISSING_BANK_TRANSACTION]
        unmatched_amount += float(pd.to_numeric(ledger_only["ledger_normalized_amount"], errors="coerce").abs().sum())

    mismatched = audit[audit["reconciliation_type"] == MISMATCH]
    mismatched_amount = float(pd.to_numeric(mismatched.get("amount_difference", pd.Series(dtype=float)), errors="coerce").abs().sum())
    exception_records = _exception_record_count(audit)
    total_source_records = bank_total + ledger_total

    values = {
        "total_bank_transactions": bank_total,
        "total_ledger_transactions": ledger_total,
        "matched_count": matched_count,
        "mismatched_count": mismatched_count,
        "missing_count": missing_count,
        "duplicate_count": duplicate_count,
        "ambiguous_count": ambiguous_count,
        "automatic_match_rate": matched_count / bank_total if bank_total else 0.0,
        "exception_rate": exception_records / total_source_records if total_source_records else 0.0,
        "fuzzy_match_count": fuzzy_count,
        "exact_match_count": exact_count,
        "average_fuzzy_score_for_fuzzy_matches": avg_fuzzy,
        "total_unmatched_amount": round(unmatched_amount, 2),
        "total_mismatched_amount": round(mismatched_amount, 2),
        "exception_transaction_count": exception_records,
        "automatic_match_rate_denominator": "distinct bank transactions",
        "exception_rate_denominator": "distinct bank plus ledger transactions",
        "amount_basis": "absolute currency amounts; unmatched sums one-sided rows; mismatched sums absolute pair differences",
    }
    return pd.DataFrame([values])


def build_effort_comparison(
    audit: pd.DataFrame,
    *,
    manual_minutes_per_transaction: float = config.MANUAL_REVIEW_MINUTES_PER_TRANSACTION,
    manual_minutes_per_exception: float = config.MANUAL_REVIEW_MINUTES_PER_EXCEPTION,
    automated_overhead_minutes: float = config.AUTOMATED_PROCESSING_OVERHEAD_MINUTES,
) -> pd.DataFrame:
    """Calculate an assumption-based effort comparison from measured row counts.

    Source transaction counts and exception counts come from the audit. All
    minute values are caller-configurable assumptions, not observed timings.
    """
    assumptions = {
        "manual_minutes_per_transaction": manual_minutes_per_transaction,
        "manual_minutes_per_exception": manual_minutes_per_exception,
        "automated_overhead_minutes": automated_overhead_minutes,
    }
    for name, value in assumptions.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")

    bank_count = _transaction_count(audit, "bank_transaction_id")
    ledger_count = _transaction_count(audit, "ledger_transaction_id")
    source_count = bank_count + ledger_count
    exception_count = _exception_record_count(audit)
    baseline_minutes = source_count * manual_minutes_per_transaction
    automated_minutes = automated_overhead_minutes + exception_count * manual_minutes_per_exception
    reduction_minutes = baseline_minutes - automated_minutes
    reduction_percent = (reduction_minutes / baseline_minutes * 100.0) if baseline_minutes else 0.0
    return pd.DataFrame([{
        "estimate_basis": "Illustrative time assumptions applied to measured transaction and exception counts; not measured business impact",
        "total_bank_transactions": bank_count,
        "total_ledger_transactions": ledger_count,
        "total_source_transactions": source_count,
        "exception_review_units": exception_count,
        "manual_minutes_per_transaction_assumption": manual_minutes_per_transaction,
        "manual_minutes_per_exception_assumption": manual_minutes_per_exception,
        "automated_overhead_minutes_per_run_assumption": automated_overhead_minutes,
        "baseline_manual_effort_minutes": round(baseline_minutes, 2),
        "automated_workflow_effort_minutes": round(automated_minutes, 2),
        "estimated_effort_reduction_minutes": round(reduction_minutes, 2),
        "estimated_effort_reduction_percent": round(reduction_percent, 2),
        "baseline_formula": "(bank transaction rows + ledger transaction rows) * manual minutes per transaction",
        "automated_formula": "automated overhead minutes per run + unique exception source rows * manual minutes per exception",
        "reduction_formula": "baseline manual effort - automated workflow effort",
        "reduction_percent_formula": "(effort reduction / baseline manual effort) * 100",
    }])


def _html_document(summary: pd.DataFrame, discrepancies: pd.DataFrame, effort: pd.DataFrame) -> str:
    """Render a dependency-free HTML view using Pandas' escaped table output."""
    summary_html = summary.to_html(index=False, border=0, classes="summary", escape=True, na_rep="")
    effort_html = effort.to_html(index=False, border=0, classes="effort", escape=True, na_rep="")
    if discrepancies.empty:
        discrepancies_html = "<p>No discrepancies were recorded.</p>"
    else:
        discrepancies_html = discrepancies.to_html(index=False, border=0, classes="discrepancies", escape=True, na_rep="")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bank-to-Ledger Reconciliation Report</title>
  <style>
    body {{ color: #202833; font: 14px/1.5 Arial, sans-serif; margin: 2rem auto; max-width: 1200px; padding: 0 1rem; }}
    h1, h2 {{ color: #17324d; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ border-collapse: collapse; margin: 1rem 0 2rem; width: 100%; }}
    th, td {{ border: 1px solid #d8e0e8; padding: .55rem .7rem; text-align: left; vertical-align: top; }}
    th {{ background: #edf3f8; }}
    tbody tr:nth-child(even) {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <h1>Bank-to-Ledger Reconciliation Report</h1>
  <h2>Summary</h2>
  <div class="table-wrap">{summary_html}</div>
  <h2>Estimated manual effort comparison</h2>
  <p>This estimate combines measured transaction counts with explicitly assumed review times. It is not a measured business result.</p>
  <div class="table-wrap">{effort_html}</div>
  <h2>Discrepancies and items for review</h2>
  <div class="table-wrap">{discrepancies_html}</div>
</body>
</html>
"""


def generate_reports(
    audit: pd.DataFrame,
    output_dir: str | Path = config.REPORTS_DIR,
    *,
    manual_minutes_per_transaction: float = config.MANUAL_REVIEW_MINUTES_PER_TRANSACTION,
    manual_minutes_per_exception: float = config.MANUAL_REVIEW_MINUTES_PER_EXCEPTION,
    automated_overhead_minutes: float = config.AUTOMATED_PROCESSING_OVERHEAD_MINUTES,
) -> dict[str, Path]:
    """Write the full audit, exception-only report, summary, and HTML report.

    Summary rates and amounts are calculated from the supplied reconciliation
    output; no performance figures are embedded or hard-coded.
    """
    required = {"reconciliation_type", "bank_transaction_id", "ledger_transaction_id"}
    missing = sorted(required - set(audit.columns))
    if missing:
        raise ValueError(f"Reconciliation audit is missing required columns: {', '.join(missing)}")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    discrepancy_report = _build_discrepancy_report(audit)
    summary = build_summary(audit)
    effort = build_effort_comparison(
        audit,
        manual_minutes_per_transaction=manual_minutes_per_transaction,
        manual_minutes_per_exception=manual_minutes_per_exception,
        automated_overhead_minutes=automated_overhead_minutes,
    )
    # Keep the familiar summary CSV useful while preserving a separate detailed
    # assumptions sheet for reviewers who want to inspect the estimate inputs.
    for column, value in effort.iloc[0].items():
        if column not in summary:
            summary[column] = value
    reconciliation_report = audit.copy()
    reconciliation_report["decision_explanation"] = [
        _decision_text(row) for _, row in reconciliation_report.iterrows()
    ]
    outputs = {
        "reconciliation_report.csv": reconciliation_report,
        "discrepancy_report.csv": discrepancy_report,
        "reconciliation_summary.csv": summary,
        "manual_effort_comparison.csv": effort,
    }
    paths: dict[str, Path] = {}
    for filename, frame in outputs.items():
        path = destination / filename
        frame.to_csv(path, index=False)
        paths[filename] = path

    html_path = destination / "reconciliation_report.html"
    html_path.write_text(_html_document(summary, discrepancy_report, effort), encoding="utf-8")
    paths[html_path.name] = html_path
    return paths

