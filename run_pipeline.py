"""Run the complete synthetic bank-to-ledger reconciliation workflow."""
import argparse
from pathlib import Path

from src.data_generator import DEFAULT_SEED
from src.pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="seed for reproducible synthetic data")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=None,
        help="report output directory (defaults to the project's reports/ directory)",
    )
    args = parser.parse_args()
    options = {"seed": args.seed}
    if args.reports_dir is not None:
        options["reports_dir"] = args.reports_dir
    run_pipeline(**options)


if __name__ == "__main__":
    main()

