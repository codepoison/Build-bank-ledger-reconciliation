# Interview guide: Bank-to-Ledger Reconciliation

This guide describes the current repository implementation. All financial-looking records are synthetic/demo data generated locally; no real banking or customer information is included. The system reads and writes CSV files and does not use a database.

## What the repository demonstrates

| Area | Evidence in the repository | What it demonstrates |
| --- | --- | --- |
| Python | `src/` modules, `run_pipeline.py` | Reusable functions, type hints, dictionaries, `pathlib`, CLI arguments, seeded generation, modular orchestration, and readable exception messages. |
| Pandas | `src/cleaning.py`, `src/matching.py`, `src/reconciliation.py`, `src/reporting.py`, `src/benchmark.py` | CSV/DataFrame input, schema checks, Series transformations, grouping, ID-based lookup enrichment, metric tables, and CSV/HTML output. |
| NumPy | `src/data_generator.py` | `np.random.default_rng(seed)` provides a reproducible random stream for selecting synthetic transaction dates. It is used in generation, not for benchmark metric calculations. |
| Fuzzy matching | `src/matching.py` | RapidFuzz `WRatio` for descriptions/narrations and `ratio` for references, evaluated alongside amount and date evidence. |
| Data cleaning | `src/cleaning.py` | Required-column validation; date parsing and calendar-date normalization; currency-like amount parsing and two-decimal rounding; case, whitespace, and punctuation normalization; preservation of raw columns. |
| Automation | `src/pipeline.py`, `run_pipeline.py` | One command regenerates seeded CSVs, tunes the benchmark threshold, reconciles generated inputs, and writes audit, discrepancy, summary, effort, and benchmark outputs. |
| Exception handling | `src/cleaning.py`, `src/matching.py`, `src/reconciliation.py`, `src/benchmark.py`, `src/pipeline.py` | Clear validation errors for missing/invalid fields, invalid IDs and ground truth; file-read errors are wrapped; matcher statuses surface data exceptions; benchmark/pipeline restore temporary threshold configuration in `finally`. |
| Testing | `tests/` | Pytest coverage for cleaning, amount/date tolerance, exact/fuzzy matches, mismatch, missing, duplicates, ambiguity, reporting, threshold evaluation, and orchestration. |
| Business thinking | `src/matching.py`, `src/reconciliation.py`, `src/reporting.py`, `src/config.py` | Prioritizes strong evidence, flags uncertainty instead of forcing a pairing, explains exceptions, preserves evidence for audit, tunes to a precision floor, and separates measured counts from assumed review-time inputs. |

## Likely interview questions and concise answers

### Project and implementation

**1. What business problem does this project address?**

It automates comparing bank-statement transactions with internal-ledger entries. It identifies likely matches and makes missing, mismatched, duplicate, and ambiguous cases reviewable with reasons and source evidence.

**2. Walk me through the pipeline.**

`run_pipeline.py` calls the orchestrator in `src/pipeline.py`. It generates synthetic inputs, runs the benchmark threshold sweep, cleans both CSV sources, applies the selected threshold for reconciliation, and writes the audit and report files.

**3. What parts demonstrate Python?**

The project is split into focused modules and reusable functions, uses `pathlib` paths, typed function signatures, a deterministic generator, command-line argument parsing, and explicit error handling. `run_pipeline.py` is the CLI entry point.

**4. Where does Pandas help?**

Pandas loads and writes CSVs, holds cleaned records in DataFrames, transforms columns, groups benchmark outcomes, calculates summary values, and renders HTML tables. It is used across cleaning, matching, reconciliation, reporting, and benchmarking.

**5. How does the project use NumPy?**

`src/data_generator.py` uses `np.random.default_rng(seed)` as a seeded random stream for selecting synthetic dates. The metrics and reconciliation logic are calculated with Python and Pandas; NumPy is not used for those calculations.

**6. What demonstrates automation?**

The command `python run_pipeline.py` generates synthetic CSVs, benchmarks and tunes the fuzzy threshold, reconciles the bank and ledger files, and creates the reports without manual file-by-file processing.

**7. What cleaning is applied, and why preserve raw values?**

The cleaner validates required columns, parses dates, rounds currency-like amounts to cents, normalizes description text and references, and adds normalized columns while retaining original source columns. Preserving originals lets a reviewer compare the exact imported text with the values used for matching.

**8. How is date tolerance handled?**

Candidate generation considers ledger rows within the configured date window (three days by default). The matcher calculates the absolute calendar-day difference and records it. Dates are normalized to midnight during cleaning.

**9. How are exceptions handled in code?**

Invalid schemas, dates, amounts, IDs, and benchmark ground-truth relationships raise descriptive `ValueError`s. CSV read errors are caught and re-raised with source context. At the data level, mismatched, missing, and duplicate/ambiguous conditions are emitted as explicit statuses and reasons rather than silently discarded.

**10. How do you test the system?**

Pytest tests cover normalization, preservation of original values, invalid inputs, amount/date tolerances, exact and fuzzy match paths, mismatches, one-sided records, duplicate and ambiguous cases, report generation, benchmark outputs, and the pipeline. Run them with `python -m pytest`.

### Matching logic, duplicates, and auditability

**11. How does candidate generation avoid comparing every bank row to every ledger row?**

The matcher indexes ledger rows by normalized date and checks only rows in the bank transaction’s configured date window. It then computes evidence for those candidates. It does not perform a full bank-by-ledger cross join.

**12. What is the matching sequence?**

For date-window candidates, the matcher first accepts matching normalized references with amount within tolerance (Stage 1). Otherwise it accepts amount/date-compatible candidates whose RapidFuzz description score meets the fuzzy threshold (Stage 2), or candidates meeting the configured combined fuzzy-score and confidence conditions (Stage 3). A likely counterpart with an amount outside tolerance can be classified as a mismatch.

**13. What exactly is the “exact” match?**

In this code, Stage 1 means normalized references are equal, the absolute amount difference is within the configured cent tolerance, and the dates are inside the configured window. It does not require identical descriptions. The reconciliation layer labels that method `EXACT_MATCH`.

**14. Which RapidFuzz measures are used?**

Descriptions/narrations are compared with `rapidfuzz.fuzz.WRatio`; non-empty normalized references are also compared with `fuzz.ratio` for combined evidence and mismatch identification. Normalization happens before those comparisons.

**15. How is the combined candidate score calculated?**

The code weights description similarity at 0.65, reference similarity at 0.25, amount evidence at 0.05, and date evidence at 0.05. Amount evidence is 100 when within tolerance and 0 otherwise. Date evidence declines with distance within the configured tolerance. The weighted score is normalized to a confidence value from 0 to 1.

**16. What happens when several candidates look plausible?**

The matcher emits `DUPLICATE` rows for the plausible candidates with reasons that say multiple ledger candidates exist or multiple bank rows claim a ledger row. The reconciliation layer labels the first condition as ambiguous. It does not auto-select one candidate when the evidence is contested.

**17. How is a likely amount mismatch distinguished from a missing transaction?**

Within the date candidate window, the matcher looks for amount-out-of-tolerance records with an exact reference, or with both sufficiently similar description and reference scores. It reports a likely pair as `MISMATCHED`; otherwise it can report no candidate as missing. A mismatch candidate is filtered if another bank row has an acceptable claim on that ledger row.

**18. How do you ensure records are not silently lost?**

Reconciliation checks that source IDs are present and unique, attaches source fields and row numbers to result rows, and emits missing records on either side. The audit output includes source IDs and original/normalized fields. Duplicate and ambiguous candidates remain represented as rows.

**19. What makes the output auditable?**

Each result has the bank/ledger IDs, reconciliation status/type, match method, scores, confidence, amount and date differences, a rule/threshold description, exception reason, source row numbers, and attached source fields. The discrepancy report presents human-readable reasons separately.

### Benchmark, 90%+, false positives, and threshold tuning

**20. Does this repository achieve a 90%+ automatic match rate?**

No. The current included 300-case benchmark reports **58.33% automatic match rate** (175 of 300 bank rows). `BENCHMARK_TARGET_AUTOMATIC_MATCH_RATE = 0.90` is a target used to decide when to print an explanatory message; it is not a measured result or a tuning constraint.

**21. Why is the benchmark automatic match rate below 90%?**

The balanced benchmark intentionally includes 125 bank rows expected to be handled as exceptions: 25 amount mismatches, 25 bank-only rows, 50 duplicate bank rows, and 25 ambiguous rows. They should not count as automatic matches. The measured rate denominator is all 300 bank rows. The current benchmark reports no case-level failures, but that result is only on the generated controlled cases.

**22. How was the benchmark created?**

The local generator uses a deterministic seed and creates 300 benchmark cases, with 25 cases in each of 12 scenarios. A ground-truth CSV records expected outcomes and IDs for every bank and ledger source row. Inputs and truth are generated together, not taken from real transactions.

**23. How is the threshold tuned?**

`src/benchmark.py` evaluates each integer fuzzy threshold from the configured minimum to maximum (0–100 by default). It selects the threshold with the highest measured automatic match rate among those meeting the 98% precision floor; ties favor recall, then precision, then a stricter threshold. It writes the full sweep and selected metrics.

**24. What threshold did the current included benchmark select?**

It selected 77. On this generated benchmark it reported 100% precision and 100% recall, with zero false positives and zero false negatives. These are benchmark results, not a guarantee on future or real-world transactions.

**25. What are the risks in claiming zero false positives?**

The data is synthetic, controlled, and generated from known scenarios, so zero observed false positives does not establish zero production risk. The benchmark is also used to select the threshold and report performance; there is no independent holdout set, so the result may be optimistic. I would validate on independently labeled data before making an operational claim.

**26. Why optimize for a precision floor?**

An incorrect automatic pairing can hide a real discrepancy, so the code limits threshold selection to observed candidates meeting a configured precision floor, then maximizes coverage among them. The floor is configurable. The current benchmark’s high precision does not establish that 98% is optimal for a real business risk profile.

**27. Does threshold tuning optimize every matching rule?**

No. The sweep changes `FUZZY_MATCH_THRESHOLD`. Other controls, such as date and amount tolerance, combined-stage score/confidence, and mismatch thresholds, come from `src/config.py` and remain fixed during the sweep.

**28. How are precision, recall, and false-positive rates defined here?**

The benchmark scores bank rows against expected statuses and expected bank-ledger pairs. A true positive is a correctly predicted matched bank row paired to the expected ledger ID. Precision is TP/(TP+FP), recall is TP/(TP+FN), false-positive rate is FP/(FP+TN), and false-negative rate is FN/(FN+TP). The metrics are written to `reports/benchmark_metrics.csv`.

**29. How would you improve the benchmark before using it for a real decision?**

I would create an independently labeled holdout set, keep it separate from threshold selection, broaden variation beyond the generator’s templates, and report results by scenario. I would also test performance across multiple seeds and review false-positive pairs with domain experts.

### Business and product thinking

**30. How does this show business thinking?**

The project treats uncertain records as review work rather than forcing a match, gives exceptions understandable explanations, and keeps matching evidence for audit. It also reports assumptions separately from measured transaction counts in the manual-effort estimate.

**31. What is the manual-effort reduction, and is it measured?**

It is an assumption-based estimate, not a measured saving. The code calculates baseline effort as total source rows times assumed manual minutes per transaction; automated effort is assumed processing overhead plus unique exception rows times assumed review minutes per exception. The estimate can be negative. Current defaults and counts are recorded in `manual_effort_comparison.csv`.

**32. What are the main limitations?**

The benchmark is synthetic and not an independent holdout; configurable tolerances need real-domain validation; complex cases such as split settlements or reversals are not specifically modeled; and the system produces reports rather than a human review/approval interface.

**33. What would you build next?**

I would first evaluate on independently labeled, safely governed data and isolate tuning from evaluation. Then I would add scenario-level diagnostics and threshold stability checks before extending rules for real payment conventions. A review workflow and feedback capture could follow if the evaluation supports it.

## Interview honesty checklist

- Say **58.33% automatic match rate**, not 90%+. The 90% value is configured as a target only.
- Qualify the benchmark’s 100% precision/recall as results on the included synthetic cases.
- Do not claim the benchmark proves production performance; it is used for threshold selection and is not an independent holdout.
- Describe NumPy accurately: it supplies a seeded date-selection random stream in the synthetic generator, not the matching or metrics calculations.
- Describe effort figures as calculations from explicit assumptions, not observed business savings.
- Emphasize that ambiguous and duplicate candidates are surfaced for review rather than silently resolved.

