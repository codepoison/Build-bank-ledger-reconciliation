"""Configuration for the bank-to-ledger reconciliation project."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
GENERATED_DATA_DIR = DATA_DIR / "generated"
TEST_DATA_DIR = DATA_DIR / "test"
REPORTS_DIR = PROJECT_ROOT / "reports"

BANK_CSV = GENERATED_DATA_DIR / "bank_statement.csv"
LEDGER_CSV = GENERATED_DATA_DIR / "ledger.csv"

# Matching rules are centralized here so benchmark tuning is explicit.
MATCH_DATE_TOLERANCE_DAYS = 3
MATCH_AMOUNT_TOLERANCE = 0.01
# Selected by the reproducible benchmark threshold sweep in reports/.
FUZZY_MATCH_THRESHOLD = 77.0
COMBINED_MATCH_MIN_FUZZY_SCORE = 65.0
COMBINED_MATCH_MIN_CONFIDENCE = 0.94
MISMATCH_MIN_FUZZY_SCORE = 78.0
MISMATCH_MIN_REFERENCE_SCORE = 95.0

# Benchmark tuning evaluates every integer RapidFuzz threshold in this range
# and chooses the highest automatic rate that meets the precision floor.
BENCHMARK_THRESHOLD_MIN = 0
BENCHMARK_THRESHOLD_MAX = 100
BENCHMARK_MIN_PRECISION = 0.98
BENCHMARK_MINIMUM_CASES = 250
BENCHMARK_TARGET_AUTOMATIC_MATCH_RATE = 0.90

# Reproducible synthetic dataset sizes.
PRODUCTION_CASES_PER_SCENARIO = 30
BENCHMARK_CASES_PER_SCENARIO = 25
SAMPLE_CASES_PER_SCENARIO = 1
SAMPLE_ROWS_PER_SOURCE = 8

# Illustrative effort assumptions only; these are not measured business timings.
MANUAL_REVIEW_MINUTES_PER_TRANSACTION = 2.0
MANUAL_REVIEW_MINUTES_PER_EXCEPTION = 5.0
AUTOMATED_PROCESSING_OVERHEAD_MINUTES = 10.0

# Combined stage weights; keep their sum at 1.0.
COMBINED_TEXT_WEIGHT = 0.65
COMBINED_REFERENCE_WEIGHT = 0.25
COMBINED_AMOUNT_WEIGHT = 0.05
COMBINED_DATE_WEIGHT = 0.05

