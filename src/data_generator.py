"""Create reproducible, entirely synthetic bank and ledger CSV datasets.

The benchmark includes row-level ground truth so matching behavior can be
measured later without treating the generated transactions as real accounts.
"""
from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src import config

DEFAULT_SEED = 20261008
SCENARIO_NAMES = (
    "exact_match",
    "formatting_difference",
    "minor_spelling_difference",
    "extra_spaces",
    "capitalization_difference",
    "punctuation_difference",
    "date_within_tolerance",
    "amount_mismatch",
    "bank_only",
    "ledger_only",
    "duplicate",
    "ambiguous",
)

_MERCHANTS = (
    "Northstar Office Supplies", "Greenfield Utilities", "Metro Fuel",
    "Harbor Freight Services", "Pine Street Cafe", "Summit Cloud Hosting",
    "Riverbend Property Group", "Cedar Medical Services", "Brightline Telecom",
    "Oak & Stone Consulting", "Westlake Transport", "Bluebird Software",
)
_CATEGORIES = (
    "Office Supplies", "Utilities", "Travel", "Freight", "Meals",
    "Software", "Rent", "Healthcare", "Telecommunications", "Professional Fees",
)
_BANK_COLUMNS = ["bank_transaction_id", "transaction_date", "amount", "description", "reference", "transaction_type"]
_LEDGER_COLUMNS = ["ledger_transaction_id", "ledger_date", "amount", "narration", "reference", "account_category"]
_TRUTH_COLUMNS = ["case_id", "record_type", "record_id", "expected_status", "expected_match_id", "scenario"]


def _amount(rng: random.Random) -> float:
    """Return a plausible signed transaction amount, rounded to cents."""
    value = round(rng.uniform(12.0, 4200.0), 2)
    return value if rng.random() < 0.58 else -value


def _make_case_data(
    seed: int,
    cases_per_scenario: int | Mapping[str, int],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build controlled examples and expected outcomes for each source row."""
    rng = random.Random(seed)
    # NumPy supplies an independent reproducible stream for selecting dates.
    date_rng = np.random.default_rng(seed)
    bank_rows: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    truth_rows: list[dict[str, Any]] = []
    origin = date(2025, 1, 1)
    case_number = 0

    def bank_record(txn_id: str, day: date, amount: float, text: str, ref: str) -> dict[str, Any]:
        return {
            "bank_transaction_id": txn_id,
            "transaction_date": day.isoformat(),
            "amount": amount,
            "description": text,
            "reference": ref,
            "transaction_type": "credit" if amount > 0 else "debit",
        }

    def ledger_record(txn_id: str, day: date, amount: float, text: str, ref: str, category: str) -> dict[str, Any]:
        return {
            "ledger_transaction_id": txn_id,
            "ledger_date": day.isoformat(),
            "amount": amount,
            "narration": text,
            "reference": ref,
            "account_category": category,
        }

    for scenario in SCENARIO_NAMES:
        scenario_count = cases_per_scenario.get(scenario, 0) if isinstance(cases_per_scenario, Mapping) else cases_per_scenario
        for scenario_index in range(scenario_count):
            case_number += 1
            case_id = f"CASE-{case_number:04d}"
            bank_id, ledger_id = f"B{case_number:06d}", f"L{case_number:06d}"
            merchant = rng.choice(_MERCHANTS)
            category = rng.choice(_CATEGORIES)
            day = origin + timedelta(days=int(date_rng.integers(0, 365)))
            amount = _amount(rng)
            ref = f"SYN-{case_number:06d}"
            bank_text = merchant
            ledger_text = merchant
            ledger_reference = ref
            ledger_day, ledger_amount = day, amount
            bank_match_status, ledger_match_status = "Matched", "Matched"
            expected_ledger_id: str | None = ledger_id
            expected_bank_id: str | None = bank_id
            additional_ledgers: list[dict[str, Any]] = []
            additional_banks: list[dict[str, Any]] = []

            if scenario == "formatting_difference":
                ledger_text = merchant.replace(" ", "-").upper()
                ledger_reference = f"LED-{ref}"
            elif scenario == "minor_spelling_difference":
                if scenario_index % 3 == 0:
                    ledger_text = merchant[:-1] + ("x" if merchant[-1].lower() != "x" else "z")
                elif scenario_index % 3 == 1:
                    ledger_text = merchant[:-2] + "xx"
                else:
                    ledger_text = merchant[:-4] + "xxxx"
                ledger_reference = f"LED-{ref}"
            elif scenario == "extra_spaces":
                bank_text = f"  {merchant.replace(' ', '   ')}  "
                ledger_text = f" {merchant} "
                ledger_reference = f"LED-{ref}"
            elif scenario == "capitalization_difference":
                ledger_text = merchant.swapcase()
                ledger_reference = f"LED-{ref}"
            elif scenario == "punctuation_difference":
                ledger_text = merchant.replace("&", "and").replace(" ", ". ") + ","
                ledger_reference = f"LED-{ref}"
            elif scenario == "date_within_tolerance":
                ledger_day = day + timedelta(days=rng.choice((-2, -1, 1, 2)))
            elif scenario == "amount_mismatch":
                ledger_amount = round(amount + (7.35 if amount > 0 else -7.35), 2)
                bank_match_status = ledger_match_status = "Mismatched"
            elif scenario == "bank_only":
                bank_match_status, expected_ledger_id = "Missing", None
            elif scenario == "ledger_only":
                ledger_match_status, expected_bank_id = "Missing", None
            elif scenario == "duplicate":
                # Two bank rows describe the same posting; the second is a duplicate.
                bank_match_status = "Duplicate"
                ledger_match_status = "Duplicate"
                additional_banks.append(bank_record(f"{bank_id}-DUP", day, amount, merchant, ref))
            elif scenario == "ambiguous":
                # Two equally plausible ledger candidates make this intentionally unresolved.
                bank_match_status = "Ambiguous"
                ledger_match_status = "Ambiguous"
                additional_ledgers.append(ledger_record(f"{ledger_id}-ALT", ledger_day, amount, merchant, ref, category))

            if scenario != "ledger_only":
                bank_rows.append(bank_record(bank_id, day, amount, bank_text, ref))
                truth_rows.append({"case_id": case_id, "record_type": "bank", "record_id": bank_id,
                                   "expected_status": bank_match_status, "expected_match_id": expected_ledger_id,
                                   "scenario": scenario})
            if scenario == "duplicate":
                bank_rows.extend(additional_banks)
                truth_rows.append({"case_id": case_id, "record_type": "bank", "record_id": additional_banks[0]["bank_transaction_id"],
                                   "expected_status": "Duplicate", "expected_match_id": ledger_id, "scenario": scenario})

            if scenario != "bank_only":
                ledger_rows.append(ledger_record(ledger_id, ledger_day, ledger_amount, ledger_text, ledger_reference, category))
                truth_rows.append({"case_id": case_id, "record_type": "ledger", "record_id": ledger_id,
                                   "expected_status": ledger_match_status, "expected_match_id": expected_bank_id,
                                   "scenario": scenario})
            if scenario == "ambiguous":
                ledger_rows.extend(additional_ledgers)
                truth_rows.append({"case_id": case_id, "record_type": "ledger", "record_id": additional_ledgers[0]["ledger_transaction_id"],
                                   "expected_status": "Ambiguous", "expected_match_id": bank_id, "scenario": scenario})

    return (
        pd.DataFrame(bank_rows, columns=_BANK_COLUMNS),
        pd.DataFrame(ledger_rows, columns=_LEDGER_COLUMNS),
        pd.DataFrame(truth_rows, columns=_TRUTH_COLUMNS),
    )


def generate_datasets(
    output_root: Path | str | None = None,
    *,
    seed: int = DEFAULT_SEED,
    production_cases_per_scenario: int = config.PRODUCTION_CASES_PER_SCENARIO,
    benchmark_cases_per_scenario: int | Mapping[str, int] | None = None,
) -> dict[str, Path]:
    """Write full synthetic source files, benchmark truth, and readable samples.

    The default benchmark has 300 controlled scenario cases, independently generated from
    the larger source dataset. Each ground-truth row refers to one source row.
    """
    root = Path(output_root) if output_root is not None else config.PROJECT_ROOT
    generated_dir, test_dir = root / "data" / "generated", root / "data" / "test"
    generated_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    bank, ledger, _ = _make_case_data(seed, production_cases_per_scenario)
    if benchmark_cases_per_scenario is None:
        # Equal scenario counts keep the headline rate from being driven by a
        # hand-selected proportion of easy matches versus exception cases.
        benchmark_counts = {name: config.BENCHMARK_CASES_PER_SCENARIO for name in SCENARIO_NAMES}
    elif isinstance(benchmark_cases_per_scenario, int):
        benchmark_counts = {name: benchmark_cases_per_scenario for name in SCENARIO_NAMES}
    else:
        benchmark_counts = benchmark_cases_per_scenario
    benchmark_bank, benchmark_ledger, truth = _make_case_data(seed + 1, benchmark_counts)
    # A compact, legible subset for manual inspection; separate from the benchmark.
    sample_bank, sample_ledger, sample_truth = _make_case_data(seed + 2, config.SAMPLE_CASES_PER_SCENARIO)
    sample_bank = sample_bank.head(config.SAMPLE_ROWS_PER_SOURCE)
    sample_ledger = sample_ledger.head(config.SAMPLE_ROWS_PER_SOURCE)
    sample_truth = sample_truth[sample_truth["record_id"].isin(
        set(sample_bank["bank_transaction_id"]) | set(sample_ledger["ledger_transaction_id"]) )]

    datasets = {
        generated_dir / "bank_statement.csv": bank,
        generated_dir / "ledger.csv": ledger,
        test_dir / "benchmark_bank.csv": benchmark_bank,
        test_dir / "benchmark_ledger.csv": benchmark_ledger,
        test_dir / "benchmark_ground_truth.csv": truth,
        test_dir / "sample_bank_statement.csv": sample_bank,
        test_dir / "sample_ledger.csv": sample_ledger,
        test_dir / "sample_ground_truth.csv": sample_truth,
    }
    for path, frame in datasets.items():
        frame.to_csv(path, index=False)
    return {name: path for name, path in ((path.name, path) for path in datasets)}


def main() -> None:
    """CLI entry point for regenerating all synthetic datasets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="seed for reproducible generation")
    args = parser.parse_args()
    created = generate_datasets(seed=args.seed)
    print(f"Generated synthetic datasets (seed={args.seed}):")
    for name, path in created.items():
        print(f"  {path.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()

