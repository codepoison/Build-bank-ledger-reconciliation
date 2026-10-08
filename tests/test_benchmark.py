"""Tests for reproducible threshold tuning and benchmark artifacts."""
import pandas as pd

from src.benchmark import run_benchmark
from src.data_generator import generate_datasets


def test_benchmark_runs_controlled_cases_and_writes_metrics(tmp_path):
    generate_datasets(output_root=tmp_path)
    report_dir = tmp_path / "benchmark-reports"

    result = run_benchmark(
        tmp_path / "data/test/benchmark_bank.csv",
        tmp_path / "data/test/benchmark_ledger.csv",
        tmp_path / "data/test/benchmark_ground_truth.csv",
        report_dir,
    )

    assert result["metrics"]["benchmark_case_count"] >= 250
    assert 0 <= result["threshold"] <= 100
    assert (report_dir / "benchmark_metrics.csv").exists()
    assert (report_dir / "benchmark_case_results.csv").exists()
    assert (report_dir / "benchmark_failures.csv").exists()
    assert (report_dir / "benchmark_non_automatic_cases.csv").exists()
    assert (report_dir / "benchmark_non_automatic_breakdown.csv").exists()
    assert (report_dir / "benchmark_threshold_sweep.csv").exists()
    saved = pd.read_csv(report_dir / "benchmark_metrics.csv")
    assert saved.loc[0, "fuzzy_threshold"] == result["threshold"]
    failures = pd.read_csv(report_dir / "benchmark_failures.csv")
    non_automatic = pd.read_csv(report_dir / "benchmark_non_automatic_cases.csv")
    assert len(failures) == len(result["failures"])
    assert {"case_id", "scenario", "expected_status", "actual_status"}.issubset(failures.columns)
    assert len(non_automatic) == result["metrics"]["bank_transaction_count"] - result["metrics"]["automatic_match_count"]

