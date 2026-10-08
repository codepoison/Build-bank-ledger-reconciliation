"""Report generation tests using a small, deterministic reconciliation."""
import pandas as pd

from src.reconciliation import reconcile_transactions
from src.reporting import generate_reports


def test_reports_include_discrepancies_summary_and_effort_formula(tmp_path):
    bank = pd.DataFrame(
        [
            {"bank_transaction_id": "B1", "transaction_date": "2025-01-10", "amount": 100.0,
             "description": "Office Supplies", "reference": "R1", "transaction_type": "debit"},
            {"bank_transaction_id": "B2", "transaction_date": "2025-02-10", "amount": 200.0,
             "description": "Monthly Rent", "reference": "R2", "transaction_type": "debit"},
            {"bank_transaction_id": "B3", "transaction_date": "2025-03-10", "amount": 50.0,
             "description": "Unrecorded Bank Charge", "reference": "R3", "transaction_type": "debit"},
        ]
    )
    ledger = pd.DataFrame(
        [
            {"ledger_transaction_id": "L1", "ledger_date": "2025-01-10", "amount": 100.0,
             "narration": "Office Supplies", "reference": "R1", "account_category": "Office"},
            {"ledger_transaction_id": "L2", "ledger_date": "2025-02-10", "amount": 250.0,
             "narration": "Monthly Rent", "reference": "R2", "account_category": "Rent"},
            {"ledger_transaction_id": "L3", "ledger_date": "2025-04-10", "amount": 10.0,
             "narration": "Ledger Only Entry", "reference": "R4", "account_category": "Other"},
        ]
    )
    audit = reconcile_transactions(bank, ledger)

    outputs = generate_reports(
        audit,
        tmp_path,
        manual_minutes_per_transaction=2,
        manual_minutes_per_exception=5,
        automated_overhead_minutes=10,
    )

    assert set(outputs) == {
        "reconciliation_report.csv", "discrepancy_report.csv",
        "reconciliation_summary.csv", "manual_effort_comparison.csv",
        "reconciliation_report.html",
    }
    discrepancies = pd.read_csv(outputs["discrepancy_report.csv"])
    assert "Likely corresponding ledger transaction found, but amount differs by ₹50.00." in set(
        discrepancies["exception_reason"]
    )
    summary = pd.read_csv(outputs["reconciliation_summary.csv"]).iloc[0]
    assert summary["matched_count"] == 1
    assert summary["mismatched_count"] == 1
    assert summary["missing_count"] == 2
    assert summary["baseline_manual_effort_minutes"] == 12
    assert summary["automated_workflow_effort_minutes"] == 30
    assert summary["estimated_effort_reduction_minutes"] == -18
    effort = pd.read_csv(outputs["manual_effort_comparison.csv"]).iloc[0]
    assert "not measured business impact" in effort["estimate_basis"]
    html = outputs["reconciliation_report.html"].read_text(encoding="utf-8")
    assert "Estimated manual effort comparison" in html
    assert "baseline manual effort" in html

