"""Staged, explainable matching of cleaned bank and ledger transactions."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

import pandas as pd
from rapidfuzz import fuzz

from src import config
from src.cleaning import normalize_description, normalize_reference


MATCHED = "MATCHED"
MISMATCHED = "MISMATCHED"
MISSING = "MISSING"
DUPLICATE = "DUPLICATE"

OUTPUT_COLUMNS = [
    "bank_transaction_id", "ledger_transaction_id", "match_status", "match_method",
    "fuzzy_score", "candidate_score", "date_difference", "amount_difference",
    "confidence", "exception_reason",
]


def _prepare_records(records: pd.DataFrame, *, side: str) -> pd.DataFrame:
    """Copy cleaned records and fill normalized fields when raw input is supplied."""
    prepared = records.copy(deep=True).reset_index(drop=True)
    if side == "bank":
        id_column, date_column, description_column = "bank_transaction_id", "transaction_date", "description"
    else:
        id_column, date_column, description_column = "ledger_transaction_id", "ledger_date", "narration"
    required = [id_column, date_column, "amount", description_column, "reference"]
    missing = [column for column in required if column not in prepared.columns]
    if missing:
        raise ValueError(f"{side.title()} records are missing required columns: {', '.join(missing)}")

    if "normalized_date" not in prepared:
        prepared["normalized_date"] = pd.to_datetime(prepared[date_column], errors="coerce").dt.normalize()
    else:
        prepared["normalized_date"] = pd.to_datetime(prepared["normalized_date"], errors="coerce").dt.normalize()
    if "normalized_amount" not in prepared:
        prepared["normalized_amount"] = pd.to_numeric(prepared["amount"], errors="coerce").round(2)
    else:
        prepared["normalized_amount"] = pd.to_numeric(prepared["normalized_amount"], errors="coerce").round(2)
    if "normalized_description" not in prepared:
        prepared["normalized_description"] = prepared[description_column].map(normalize_description)
    if "normalized_reference" not in prepared:
        prepared["normalized_reference"] = prepared["reference"].map(normalize_reference)
    prepared["normalized_description"] = prepared["normalized_description"].fillna("").astype(str)
    prepared["normalized_reference"] = prepared["normalized_reference"].fillna("").astype(str)

    invalid = prepared[["normalized_date", "normalized_amount"]].isna().any(axis=1)
    if invalid.any():
        indices = ", ".join(str(i) for i in prepared.index[invalid][:5])
        raise ValueError(f"{side.title()} records have invalid date or amount at row index(es): {indices}")
    return prepared


def _date_difference(bank_date: pd.Timestamp, ledger_date: pd.Timestamp) -> int:
    return abs((bank_date - ledger_date).days)


def _candidate_evidence(bank_row: pd.Series, ledger_row: pd.Series) -> dict[str, Any]:
    """Score one date-blocked pair using text, reference, amount, and date evidence."""
    fuzzy_score = float(fuzz.WRatio(bank_row["normalized_description"], ledger_row["normalized_description"]))
    bank_reference = bank_row["normalized_reference"]
    ledger_reference = ledger_row["normalized_reference"]
    reference_score = float(fuzz.ratio(bank_reference, ledger_reference)) if bank_reference and ledger_reference else 0.0
    date_difference = _date_difference(bank_row["normalized_date"], ledger_row["normalized_date"])
    amount_difference = abs(float(bank_row["normalized_amount"]) - float(ledger_row["normalized_amount"]))

    amount_score = 100.0 if amount_difference <= config.MATCH_AMOUNT_TOLERANCE else 0.0
    date_tolerance = max(config.MATCH_DATE_TOLERANCE_DAYS, 1)
    date_score = max(0.0, 100.0 * (1.0 - date_difference / (date_tolerance + 1.0)))
    combined_score = (
        fuzzy_score * config.COMBINED_TEXT_WEIGHT
        + reference_score * config.COMBINED_REFERENCE_WEIGHT
        + amount_score * config.COMBINED_AMOUNT_WEIGHT
        + date_score * config.COMBINED_DATE_WEIGHT
    )
    return {
        "fuzzy_score": round(fuzzy_score, 2),
        "reference_score": round(reference_score, 2),
        "candidate_score": round(combined_score, 2),
        "date_difference": date_difference,
        "amount_difference": round(amount_difference, 2),
        "confidence": round(combined_score / 100.0, 4),
        "reference_exact": bool(bank_reference and bank_reference == ledger_reference),
    }


def _method(evidence: dict[str, Any], stage: str) -> str:
    if stage == "stage_1":
        return "stage_1_exact_reference_amount_date"
    if stage == "stage_2":
        return "stage_2_amount_date_fuzzy_description"
    if stage == "stage_3":
        return "stage_3_combined_evidence_score"
    if stage == "mismatch":
        return "likely_pair_amount_mismatch"
    raise ValueError(f"Unknown match stage: {stage}")


def _make_output_row(
    bank_id: Any,
    ledger_id: Any,
    status: str,
    method: str,
    evidence: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    evidence = evidence or {}
    return {
        "bank_transaction_id": bank_id,
        "ledger_transaction_id": ledger_id,
        "match_status": status,
        "match_method": method,
        "fuzzy_score": evidence.get("fuzzy_score"),
        "candidate_score": evidence.get("candidate_score"),
        "date_difference": evidence.get("date_difference"),
        "amount_difference": evidence.get("amount_difference"),
        "confidence": evidence.get("confidence"),
        "exception_reason": reason,
    }


def match_transactions(bank_records: pd.DataFrame, ledger_records: pd.DataFrame) -> pd.DataFrame:
    """Reconcile cleaned bank and ledger rows and return explainable pair outcomes.

    Candidate generation uses a date index to avoid a full bank-by-ledger
    cross-join. Each bank row is evaluated only against ledger rows inside the
    configured date window. Multiple acceptable candidates and many-to-one
    claims are emitted as ``DUPLICATE`` rows so ambiguity stays visible.
    """
    bank = _prepare_records(bank_records, side="bank")
    ledger = _prepare_records(ledger_records, side="ledger")

    by_date: dict[pd.Timestamp, list[int]] = defaultdict(list)
    for ledger_index, ledger_row in ledger.iterrows():
        by_date[ledger_row["normalized_date"]].append(ledger_index)

    proposals: list[dict[str, Any]] = []
    for bank_index, bank_row in bank.iterrows():
        bank_id = bank_row["bank_transaction_id"]
        bank_date = bank_row["normalized_date"]
        candidate_indices: list[int] = []
        for day_offset in range(-config.MATCH_DATE_TOLERANCE_DAYS, config.MATCH_DATE_TOLERANCE_DAYS + 1):
            candidate_indices.extend(by_date.get(bank_date + timedelta(days=day_offset), ()))

        evidence_by_index: dict[int, dict[str, Any]] = {
            ledger_index: _candidate_evidence(bank_row, ledger.loc[ledger_index])
            for ledger_index in candidate_indices
        }
        acceptable: list[tuple[int, str, dict[str, Any]]] = []
        for ledger_index, evidence in evidence_by_index.items():
            amount_ok = evidence["amount_difference"] <= config.MATCH_AMOUNT_TOLERANCE
            if not amount_ok:
                continue
            if evidence["reference_exact"]:
                stage = "stage_1"
            elif evidence["fuzzy_score"] >= config.FUZZY_MATCH_THRESHOLD:
                stage = "stage_2"
            elif (
                evidence["fuzzy_score"] >= config.COMBINED_MATCH_MIN_FUZZY_SCORE
                and evidence["confidence"] >= config.COMBINED_MATCH_MIN_CONFIDENCE
            ):
                stage = "stage_3"
            else:
                continue
            acceptable.append((ledger_index, stage, evidence))

        if acceptable:
            acceptable.sort(key=lambda item: (item[2]["candidate_score"], item[2]["fuzzy_score"]), reverse=True)
            proposals.append({
                "bank_index": bank_index,
                "bank_id": bank_id,
                "kind": "acceptable",
                "candidates": acceptable,
            })
            continue

        # Keep likely amount discrepancies as explicit exceptions rather than
        # misreporting them as one-sided missing transactions.
        mismatches = [
            (ledger_index, "mismatch", evidence)
            for ledger_index, evidence in evidence_by_index.items()
            if evidence["amount_difference"] > config.MATCH_AMOUNT_TOLERANCE
            and (
                evidence["reference_exact"]
                or (
                    evidence["fuzzy_score"] >= config.MISMATCH_MIN_FUZZY_SCORE
                    and evidence["reference_score"] >= config.MISMATCH_MIN_REFERENCE_SCORE
                )
            )
        ]
        if mismatches:
            mismatches.sort(key=lambda item: (item[2]["reference_exact"], item[2]["candidate_score"], item[2]["fuzzy_score"]), reverse=True)
            proposals.append({
                "bank_index": bank_index,
                "bank_id": bank_id,
                "kind": "mismatch",
                "candidates": mismatches,
            })
        else:
            proposals.append({"bank_index": bank_index, "bank_id": bank_id, "kind": "missing", "candidates": []})

    # A likely amount mismatch must not displace a stronger accepted pairing.
    # For example, a bank-only row with a similar narration should not turn the
    # true exact match into a duplicate merely because both share a date window.
    accepted_claims: dict[int, set[int]] = defaultdict(set)
    for proposal in proposals:
        if proposal["kind"] == "acceptable":
            for ledger_index, _, _ in proposal["candidates"]:
                accepted_claims[ledger_index].add(proposal["bank_index"])
    for proposal in proposals:
        if proposal["kind"] != "mismatch":
            continue
        proposal["candidates"] = [
            candidate for candidate in proposal["candidates"]
            if not accepted_claims.get(candidate[0], set()) - {proposal["bank_index"]}
        ]
        if not proposal["candidates"]:
            proposal["kind"] = "missing"

    # Mark any ledger row proposed by multiple acceptable bank transactions as a collision.
    ledger_claims: dict[int, set[int]] = defaultdict(set)
    for proposal in proposals:
        if proposal["kind"] != "acceptable":
            continue
        for ledger_index, _, _ in proposal["candidates"]:
            ledger_claims[ledger_index].add(proposal["bank_index"])

    results: list[dict[str, Any]] = []
    bank_indices_with_candidates: set[int] = set()
    ledger_indices_accounted: set[int] = set()
    for proposal in proposals:
        bank_index = proposal["bank_index"]
        bank_id = proposal["bank_id"]
        candidates = proposal["candidates"]
        if not candidates:
            results.append(_make_output_row(
                bank_id, None, MISSING, "no_candidate",
                None, "No ledger transaction was found within the configured date window with sufficient matching evidence.",
            ))
            continue

        bank_indices_with_candidates.add(bank_index)
        contested = len(candidates) > 1 or any(len(ledger_claims[index]) > 1 for index, _, _ in candidates)
        if contested:
            for ledger_index, stage, evidence in candidates:
                ledger_indices_accounted.add(ledger_index)
                conflict = len(ledger_claims[ledger_index]) > 1
                reason = (
                    "Multiple bank rows claim this ledger row; review the duplicate transaction evidence."
                    if conflict else "Multiple ledger candidates are plausible; no automatic pairing was selected."
                )
                results.append(_make_output_row(
                    bank_id, ledger.loc[ledger_index, "ledger_transaction_id"], DUPLICATE,
                    _method(evidence, stage), evidence, reason,
                ))
            continue

        ledger_index, stage, evidence = candidates[0]
        ledger_id = ledger.loc[ledger_index, "ledger_transaction_id"]
        ledger_indices_accounted.add(ledger_index)
        if proposal["kind"] == "mismatch":
            reason = (
                f"Likely corresponding transaction found, but the amount differs by "
                f"{evidence['amount_difference']:.2f}, beyond the configured tolerance "
                f"of {config.MATCH_AMOUNT_TOLERANCE:.2f}."
            )
            results.append(_make_output_row(bank_id, ledger_id, MISMATCHED, _method(evidence, stage), evidence, reason))
        else:
            results.append(_make_output_row(bank_id, ledger_id, MATCHED, _method(evidence, stage), evidence, ""))

    # Ledger records not involved in any proposed pair are one-sided missing rows.
    for ledger_index, ledger_row in ledger.iterrows():
        if ledger_index not in ledger_indices_accounted:
            results.append(_make_output_row(
                None, ledger_row["ledger_transaction_id"], MISSING, "no_candidate", None,
                "Ledger transaction has no acceptable bank-statement counterpart.",
            ))

    return pd.DataFrame(results, columns=OUTPUT_COLUMNS)

