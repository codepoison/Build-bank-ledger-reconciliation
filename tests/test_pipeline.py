"""End-to-end orchestration test with the expensive tuner isolated."""
from src import config
from src.data_generator import generate_datasets
from src.pipeline import run_pipeline


def test_pipeline_generates_data_reconciles_and_writes_reports(tmp_path, monkeypatch):
    def generate_small_dataset(seed):
        return generate_datasets(
            output_root=tmp_path,
            seed=seed,
            production_cases_per_scenario=1,
            benchmark_cases_per_scenario=1,
        )

    monkeypatch.setattr("src.pipeline.generate_datasets", generate_small_dataset)
    monkeypatch.setattr(config, "BANK_CSV", tmp_path / "data/generated/bank_statement.csv")
    monkeypatch.setattr(config, "LEDGER_CSV", tmp_path / "data/generated/ledger.csv")
    monkeypatch.setattr(config, "FUZZY_MATCH_THRESHOLD", 77.0)
    monkeypatch.setattr(
        "src.pipeline.run_benchmark",
        lambda **kwargs: {
            "threshold": 77,
            "metrics": {
                "benchmark_case_count": 12,
                "automatic_match_rate": 0.5,
                "precision": 1.0,
                "recall": 1.0,
            },
        },
    )
    report_dir = tmp_path / "pipeline-reports"

    result = run_pipeline(seed=123, reports_dir=report_dir, verbose=False)

    assert len(result["bank"]) >= 10
    assert len(result["ledger"]) >= 10
    assert not result["audit"].empty
    assert result["fuzzy_threshold"] == 77
    assert "estimated_effort_reduction_minutes" in result["summary"]
    assert (report_dir / "reconciliation_report.csv").exists()
    assert (report_dir / "discrepancy_report.csv").exists()
    assert (report_dir / "reconciliation_summary.csv").exists()
    assert (report_dir / "manual_effort_comparison.csv").exists()

