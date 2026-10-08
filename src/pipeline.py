"""Orchestrate synthetic-data generation, reconciliation, benchmarking, and reports."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from src import config
from src.benchmark import run_benchmark
from src.cleaning import clean_bank_data, clean_ledger_data
from src.data_generator import DEFAULT_SEED, generate_datasets
from src.reconciliation import reconcile_cleaned_data
from src.reporting import generate_reports


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(config.PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def run_pipeline(
    *,
    seed: int = DEFAULT_SEED,
    reports_dir: str | Path = config.REPORTS_DIR,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run the complete reproducible workflow and write all audit artifacts.

    The benchmark tunes the fuzzy threshold first. That measured threshold is
    then applied to the full generated dataset for this run. All temporary
    configuration changes are restored before returning.
    """
    output_dir = Path(reports_dir)
    generate_datasets(seed=seed)

    benchmark = run_benchmark(output_dir=output_dir, verbose=False)
    configured_threshold = config.FUZZY_MATCH_THRESHOLD
    config.FUZZY_MATCH_THRESHOLD = float(benchmark["threshold"])
    try:
        bank = clean_bank_data(config.BANK_CSV)
        ledger = clean_ledger_data(config.LEDGER_CSV)
        audit = reconcile_cleaned_data(bank, ledger)
        paths = generate_reports(audit, output_dir)
        # Read the report's enriched summary so terminal output includes the
        # effort estimate generated alongside the other summary metrics.
        summary = pd.read_csv(paths["reconciliation_summary.csv"]).iloc[0].to_dict()
    finally:
        config.FUZZY_MATCH_THRESHOLD = configured_threshold

    results: dict[str, Any] = {
        "bank": bank,
        "ledger": ledger,
        "audit": audit,
        "summary": summary,
        "benchmark": benchmark,
        "reports": paths,
        "fuzzy_threshold": benchmark["threshold"],
    }
    if verbose:
        print(f"Bank transactions: {bank['bank_transaction_id'].nunique()}")
        print(f"Ledger transactions: {ledger['ledger_transaction_id'].nunique()}")
        print()
        print(f"Matched: {int(summary['matched_count'])}")
        print(f"Mismatched: {int(summary['mismatched_count'])}")
        print(f"Missing: {int(summary['missing_count'])}")
        print(f"Duplicate: {int(summary['duplicate_count'])}")
        print(f"Ambiguous: {int(summary['ambiguous_count'])}")
        print(f"Automatic match rate: {summary['automatic_match_rate']:.2%}")
        print(f"Exceptions requiring review: {int(summary['exception_transaction_count'])}")
        print(
            "Estimated effort reduction (assumption-based): "
            f"{summary['estimated_effort_reduction_minutes']:.2f} minutes "
            f"({summary['estimated_effort_reduction_percent']:.2f}%)"
        )
        print(
            "Benchmark: "
            f"{benchmark['metrics']['benchmark_case_count']} cases, "
            f"threshold {benchmark['threshold']}, "
            f"precision {benchmark['metrics']['precision']:.2%}, "
            f"recall {benchmark['metrics']['recall']:.2%}"
        )
        if benchmark["metrics"]["automatic_match_rate"] < config.BENCHMARK_TARGET_AUTOMATIC_MATCH_RATE:
            print(
                "Benchmark automatic match rate: "
                f"{benchmark['metrics']['automatic_match_rate']:.2%}; expected exception cases are listed in "
                f"{_relative(output_dir / 'benchmark_non_automatic_breakdown.csv')}."
            )
        print("\nReports generated:")
        report_names = [
            "reconciliation_report.csv",
            "discrepancy_report.csv",
            "reconciliation_summary.csv",
            "manual_effort_comparison.csv",
            "reconciliation_report.html",
        ]
        for filename in report_names:
            print(f"- {_relative(paths[filename])}")
        print(f"- {_relative(output_dir / 'benchmark_metrics.csv')}")

    return results

