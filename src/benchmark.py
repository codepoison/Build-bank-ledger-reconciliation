"""Tune and evaluate the reconciliation matcher against generated ground truth."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from src import config
from src.cleaning import clean_bank_data, clean_ledger_data
from src.matching import DUPLICATE, MATCHED, MISSING, MISMATCHED, match_transactions


def _status_label(rows: pd.DataFrame) -> str:
    """Collapse candidate-level rows to one interpretable outcome for an ID."""
    if rows.empty:
        return "NOT_FOUND"
    statuses = set(rows["match_status"])
    if MATCHED in statuses:
        return MATCHED
    if MISMATCHED in statuses:
        return MISMATCHED
    if DUPLICATE in statuses:
        reasons = rows.loc[rows["match_status"] == DUPLICATE, "exception_reason"].fillna("").astype(str)
        if reasons.str.contains("Multiple ledger candidates", case=False).any():
            return "AMBIGUOUS"
        return DUPLICATE
    if MISSING in statuses:
        return MISSING
    return str(next(iter(statuses)))


def _source_status_map(audit: pd.DataFrame, id_column: str) -> dict[Any, str]:
    result: dict[Any, str] = {}
    for record_id, rows in audit.dropna(subset=[id_column]).groupby(id_column, sort=False):
        result[record_id] = _status_label(rows)
    return result


def _candidate_map(audit: pd.DataFrame) -> tuple[dict[Any, set[Any]], dict[Any, set[Any]]]:
    bank_to_ledger: dict[Any, set[Any]] = {}
    ledger_to_bank: dict[Any, set[Any]] = {}
    for row in audit.itertuples(index=False):
        bank_id = getattr(row, "bank_transaction_id")
        ledger_id = getattr(row, "ledger_transaction_id")
        if pd.notna(bank_id) and pd.notna(ledger_id):
            bank_to_ledger.setdefault(bank_id, set()).add(ledger_id)
            ledger_to_bank.setdefault(ledger_id, set()).add(bank_id)
    return bank_to_ledger, ledger_to_bank


def _case_results(truth: pd.DataFrame, audit: pd.DataFrame) -> pd.DataFrame:
    bank_statuses = _source_status_map(audit, "bank_transaction_id")
    ledger_statuses = _source_status_map(audit, "ledger_transaction_id")
    bank_to_ledger, ledger_to_bank = _candidate_map(audit)
    rows: list[dict[str, Any]] = []
    for expected in truth.itertuples(index=False):
        if expected.record_type == "bank":
            status_map = bank_statuses
            pair_map = bank_to_ledger
        else:
            status_map = ledger_statuses
            pair_map = ledger_to_bank
        record_id = expected.record_id
        actual_status = status_map.get(record_id, "NOT_FOUND")
        partners = sorted(pair_map.get(record_id, set()), key=str)
        expected_status = str(expected.expected_status).upper()
        expected_partner = expected.expected_match_id if pd.notna(expected.expected_match_id) else None
        if expected_status == "MATCHED":
            correct = actual_status == MATCHED and expected_partner in pair_map.get(record_id, set())
        elif expected_status in {"MISMATCHED", "DUPLICATE", "AMBIGUOUS"}:
            correct = actual_status == expected_status and (
                expected_partner is None or expected_partner in pair_map.get(record_id, set())
            )
        else:
            correct = actual_status == MISSING
        rows.append({
            "case_id": expected.case_id,
            "scenario": expected.scenario,
            "record_type": expected.record_type,
            "record_id": record_id,
            "expected_status": expected_status,
            "expected_match_id": expected_partner,
            "actual_status": actual_status,
            "candidate_match_ids": "|".join(map(str, partners)),
            "is_correct": bool(correct),
        })
    return pd.DataFrame(rows)


def _metrics(truth: pd.DataFrame, audit: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    results = _case_results(truth, audit)
    bank_results = results[results["record_type"] == "bank"].copy()
    expected_match = bank_results["expected_status"] == "MATCHED"
    predicted_match = bank_results["actual_status"] == MATCHED

    matched_pairs = set(
        zip(
            audit.loc[audit["match_status"] == MATCHED, "bank_transaction_id"],
            audit.loc[audit["match_status"] == MATCHED, "ledger_transaction_id"],
        )
    )
    expected_pairs = {
        (row.record_id, row.expected_match_id)
        for row in bank_results[bank_results["expected_status"] == "MATCHED"].itertuples(index=False)
    }
    correct_pair = pd.Series(
        [
            bool(
                status == MATCHED
                and expected_status == "MATCHED"
                and (bank_id, expected_id) in matched_pairs
            )
            for bank_id, status, expected_status, expected_id in zip(
                bank_results["record_id"], bank_results["actual_status"],
                bank_results["expected_status"], bank_results["expected_match_id"],
            )
        ],
        index=bank_results.index,
    )

    tp = int(correct_pair.sum())
    fp = int((predicted_match & ~correct_pair).sum())
    fn = int((expected_match & ~correct_pair).sum())
    tn = int((~expected_match & ~predicted_match).sum())
    bank_count = len(bank_results)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    exact_pairs = {
        (row.bank_transaction_id, row.ledger_transaction_id)
        for row in audit.itertuples(index=False)
        if row.match_status == MATCHED and row.match_method == "stage_1_exact_reference_amount_date"
    }
    fuzzy_pairs = {
        (row.bank_transaction_id, row.ledger_transaction_id)
        for row in audit.itertuples(index=False)
        if row.match_status == MATCHED and row.match_method in {
            "stage_2_amount_date_fuzzy_description", "stage_3_combined_evidence_score"
        }
    }
    exact_true = sum(pair in exact_pairs for pair in expected_pairs)
    fuzzy_true = sum(pair in fuzzy_pairs for pair in expected_pairs)
    expected_ambiguous = bank_results[bank_results["expected_status"] == "AMBIGUOUS"]
    correctly_ambiguous = int((expected_ambiguous["actual_status"] == "AMBIGUOUS").sum())

    exception_ids = set()
    for id_column, record_type in (("bank_transaction_id", "bank"), ("ledger_transaction_id", "ledger")):
        status_map = _source_status_map(audit, id_column)
        exception_ids.update(
            (record_type, record_id)
            for record_id, status in status_map.items()
            if status != MATCHED
        )
    # IDs are side-specific; count each source row once even if several candidate pairs cite it.
    source_record_count = truth[["record_type", "record_id"]].drop_duplicates().shape[0]
    exception_rate = len(exception_ids) / source_record_count if source_record_count else 0.0
    metrics: dict[str, Any] = {
        "bank_transaction_count": bank_count,
        "ledger_transaction_count": int((truth["record_type"] == "ledger").sum()),
        "benchmark_case_count": int(truth["case_id"].nunique()),
        "automatic_match_count": int(predicted_match.sum()),
        "automatic_match_rate": float(predicted_match.sum() / bank_count) if bank_count else 0.0,
        "exact_match_count": int(exact_true),
        "exact_match_rate": float(exact_true / bank_count) if bank_count else 0.0,
        "fuzzy_match_count": int(fuzzy_true),
        "fuzzy_match_rate": float(fuzzy_true / bank_count) if bank_count else 0.0,
        "true_positive_count": tp,
        "false_positive_count": fp,
        "false_negative_count": fn,
        "true_negative_count": tn,
        "precision": precision,
        "recall": recall,
        "false_positive_rate": fp / (fp + tn) if fp + tn else 0.0,
        "false_negative_rate": fn / (fn + tp) if fn + tp else 0.0,
        "ambiguous_match_count": correctly_ambiguous,
        "expected_ambiguous_count": int(len(expected_ambiguous)),
        "ambiguous_match_rate": correctly_ambiguous / len(expected_ambiguous) if len(expected_ambiguous) else 0.0,
        "exception_transaction_count": len(exception_ids),
        "exception_rate": exception_rate,
        "automatic_match_rate_denominator": "all bank transaction rows",
        "exact_and_fuzzy_rate_denominator": "all bank transaction rows",
        "exception_rate_denominator": "all unique bank and ledger source rows",
    }
    return metrics, results


def _evaluate_threshold(bank: pd.DataFrame, ledger: pd.DataFrame, truth: pd.DataFrame, threshold: int) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    config.FUZZY_MATCH_THRESHOLD = float(threshold)
    audit = match_transactions(bank, ledger)
    metrics, case_results = _metrics(truth, audit)
    metrics["fuzzy_threshold"] = threshold
    return metrics, audit, case_results


def _choose_threshold(sweep: pd.DataFrame) -> int:
    meets_floor = sweep[sweep["precision"] >= config.BENCHMARK_MIN_PRECISION]
    if not meets_floor.empty:
        # Maximize measured coverage under a precision constraint; prefer more
        # recall, then precision, then the stricter threshold for remaining ties.
        best = meets_floor.sort_values(
            ["automatic_match_rate", "recall", "precision", "fuzzy_threshold"],
            ascending=[False, False, False, False],
        ).iloc[0]
    else:
        # If the requested precision floor is unattainable, report the best
        # observed precision rather than hiding the limitation.
        best = sweep.sort_values(
            ["precision", "automatic_match_rate", "recall", "fuzzy_threshold"],
            ascending=[False, False, False, False],
        ).iloc[0]
    return int(best["fuzzy_threshold"])


def run_benchmark(
    bank_path: str | Path = config.TEST_DATA_DIR / "benchmark_bank.csv",
    ledger_path: str | Path = config.TEST_DATA_DIR / "benchmark_ledger.csv",
    truth_path: str | Path = config.TEST_DATA_DIR / "benchmark_ground_truth.csv",
    output_dir: str | Path = config.REPORTS_DIR,
    *,
    minimum_cases: int = config.BENCHMARK_MINIMUM_CASES,
    verbose: bool = True,
) -> dict[str, Any]:
    """Tune, run, score, and write reproducible benchmark results."""
    bank = clean_bank_data(bank_path)
    ledger = clean_ledger_data(ledger_path)
    truth = pd.read_csv(truth_path)
    required_truth = {"case_id", "record_type", "record_id", "expected_status", "expected_match_id", "scenario"}
    absent = sorted(required_truth - set(truth.columns))
    if absent:
        raise ValueError(f"Benchmark ground truth is missing columns: {', '.join(absent)}")
    if truth[["case_id", "record_type", "record_id", "expected_status"]].isna().any().any():
        raise ValueError("Benchmark ground truth has missing case, source ID, or expected status values.")
    if not truth["record_type"].isin({"bank", "ledger"}).all():
        raise ValueError("Benchmark ground truth record_type must be 'bank' or 'ledger'.")
    if truth.duplicated(["record_type", "record_id"]).any():
        raise ValueError("Benchmark ground truth contains duplicate source record IDs.")
    for record_type, source, id_column in (
        ("bank", bank, "bank_transaction_id"),
        ("ledger", ledger, "ledger_transaction_id"),
    ):
        source_ids = set(source[id_column])
        truth_ids = set(truth.loc[truth["record_type"] == record_type, "record_id"])
        if source_ids != truth_ids:
            missing_ids = sorted(source_ids - truth_ids)
            unknown_ids = sorted(truth_ids - source_ids)
            raise ValueError(
                f"Ground truth does not cover {record_type} source IDs exactly; "
                f"missing={missing_ids[:5]}, unknown={unknown_ids[:5]}"
            )
    case_count = truth["case_id"].nunique()
    if case_count < minimum_cases:
        raise ValueError(f"Benchmark requires at least {minimum_cases} controlled cases; found {case_count}.")

    original_threshold = config.FUZZY_MATCH_THRESHOLD
    sweep_rows: list[dict[str, Any]] = []
    threshold_range = range(config.BENCHMARK_THRESHOLD_MIN, config.BENCHMARK_THRESHOLD_MAX + 1)
    try:
        for threshold in threshold_range:
            metrics, _, _ = _evaluate_threshold(bank, ledger, truth, threshold)
            sweep_rows.append(metrics)
        sweep = pd.DataFrame(sweep_rows)
        chosen = _choose_threshold(sweep)
        final_metrics, final_audit, case_results = _evaluate_threshold(bank, ledger, truth, chosen)
        final_metrics["threshold_precision_floor"] = config.BENCHMARK_MIN_PRECISION
        final_metrics["threshold_selection_rule"] = "highest automatic match rate meeting precision floor; ties favor recall and stricter threshold"
        case_results.insert(0, "fuzzy_threshold", chosen)
        failures = case_results[~case_results["is_correct"]].copy()
        non_automatic = case_results[
            (case_results["record_type"] == "bank")
            & (case_results["actual_status"] != MATCHED)
        ].copy()
        non_automatic_breakdown = (
            non_automatic.groupby(["scenario", "expected_status", "actual_status"], dropna=False)
            .size().rename("transaction_count").reset_index()
        )
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([final_metrics]).to_csv(destination / "benchmark_metrics.csv", index=False)
        case_results.to_csv(destination / "benchmark_case_results.csv", index=False)
        failures.to_csv(destination / "benchmark_failures.csv", index=False)
        non_automatic.to_csv(destination / "benchmark_non_automatic_cases.csv", index=False)
        non_automatic_breakdown.to_csv(destination / "benchmark_non_automatic_breakdown.csv", index=False)
        sweep.to_csv(destination / "benchmark_threshold_sweep.csv", index=False)
    finally:
        config.FUZZY_MATCH_THRESHOLD = original_threshold

    if verbose:
        print(f"Benchmark cases: {final_metrics['benchmark_case_count']}")
        print(f"Selected fuzzy threshold: {chosen}")
        print(f"Automatic match rate: {final_metrics['automatic_match_rate']:.2%}")
        print(f"Precision: {final_metrics['precision']:.2%}")
        print(f"Recall: {final_metrics['recall']:.2%}")
        print(f"Exact-match rate: {final_metrics['exact_match_rate']:.2%}")
        print(f"Fuzzy-match rate: {final_metrics['fuzzy_match_rate']:.2%}")
        print(f"False-positive rate: {final_metrics['false_positive_rate']:.2%}")
        print(f"False-negative rate: {final_metrics['false_negative_rate']:.2%}")
        print(f"Ambiguous-match rate: {final_metrics['ambiguous_match_rate']:.2%}")
        print(f"Exception rate: {final_metrics['exception_rate']:.2%}")
        print(f"Failures recorded: {len(failures)}")
        if final_metrics["automatic_match_rate"] < config.BENCHMARK_TARGET_AUTOMATIC_MATCH_RATE:
            print(
                f"Automatic match rate is below 90% because {len(non_automatic)} bank rows are "
                "expected mismatch, missing, duplicate, or ambiguous exceptions; see "
                "benchmark_non_automatic_cases.csv and benchmark_non_automatic_breakdown.csv."
            )
            if failures.empty:
                print("These are expected exceptions, not benchmark misclassifications; benchmark_failures.csv is empty.")
            else:
                print("Unexpected classifications or pairings are listed in benchmark_failures.csv.")
    return {
        "metrics": final_metrics,
        "predictions": case_results,
        "failures": failures,
        "non_automatic_cases": non_automatic,
        "non_automatic_breakdown": non_automatic_breakdown,
        "threshold_sweep": sweep,
        "threshold": chosen,
        "audit": final_audit,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", type=Path, default=config.TEST_DATA_DIR / "benchmark_bank.csv")
    parser.add_argument("--ledger", type=Path, default=config.TEST_DATA_DIR / "benchmark_ledger.csv")
    parser.add_argument("--truth", type=Path, default=config.TEST_DATA_DIR / "benchmark_ground_truth.csv")
    parser.add_argument("--output-dir", type=Path, default=config.REPORTS_DIR)
    parser.add_argument("--minimum-cases", type=int, default=config.BENCHMARK_MINIMUM_CASES)
    args = parser.parse_args()
    run_benchmark(args.bank, args.ledger, args.truth, args.output_dir, minimum_cases=args.minimum_cases)


if __name__ == "__main__":
    main()

