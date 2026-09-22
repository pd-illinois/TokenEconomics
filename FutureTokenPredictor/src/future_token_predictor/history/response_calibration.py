"""Advisory exact-scope response calibration, isolated from HistoryDatabase.

Inputs are trusted, immutable response-usage-observation.v1 records. Their hashes,
scope assertion and targets are rechecked here; content hashes are integrity
checks, not signatures or permission for a browser to manufacture observations.
This module never records actuals in legacy complete-call/task history, alters a
receipt, invokes a provider, or grants quality acceptance/policy authority.

fit_response_candidate(records, *, cohort, target="output_tokens_mean") returns
a hashed response-calibration-candidate.v1, ready only after ten distinct
prediction/receipt/run records, varying predictors and the existing R² gate.
forecast_response(candidate, *, predicted_mean, cohort) returns a separate
modeled advisory response forecast; callers must persist a NEW immutable receipt.
evaluate_response_candidate(candidate, records) returns hashed held-out evidence.
At least three independent later records are required; training overlap fails
closed. WAPE is a ratio (0.1 means 10%), not cost, quality, or tail-risk coverage.
Unchanged/worse results remain explicit. No evaluation promotes a candidate.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math

import numpy as np

from .calibrator import CalibrationFactors, Calibrator, MIN_SAMPLES, _fit_linear

MIN_HELD_OUT_SAMPLES = 3
TARGETS = ("input_tokens_mean", "output_tokens_mean")
COHORT_FIELDS = ("scope", "aggregation", "provider", "model", "model_version", "archetype", "configuration_hash")
REFERENCE_FIELDS = ("observation_id", "content_hash", "prediction_id", "receipt_id", "receipt_hash", "run_id", "batch_hash")
INDEPENDENCE_FIELDS = ("prediction_id", "receipt_id", "receipt_hash", "run_id", "batch_hash")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _seal(value, kind):
    value = dict(value)
    value[kind + "_id"] = kind + "-" + _hash(value)
    value["content_hash"] = _hash(value)
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Response calibration requires finite nonnegative numeric targets")
    return value


def _time(value):
    if not isinstance(value, str):
        raise ValueError("Response calibration requires source timestamps")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("Response calibration requires timezone-aware source timestamps")
    return result


def _verified(record):
    try:
        if (record["schema_version"] != "response-usage-observation.v1"
                or record["content_hash"] != _hash({k: v for k, v in record.items() if k != "content_hash"})):
            raise ValueError("Response observation content integrity check failed")
        identity = {key: record[key] for key in ("prediction_id", "receipt_id", "receipt_hash", "run_id", "batch_hash")}
        if record["observation_id"] != "response-" + _hash(identity):
            raise ValueError("Response observation identity mismatch")
        for key in INDEPENDENCE_FIELDS:
            value = record[key]
            if key == "prediction_id" and type(value) is int and value >= 0:
                continue
            if not isinstance(value, str) or not value:
                raise ValueError("Incomplete response prediction identity")
        if record["cohort"] != {key: record[key] for key in COHORT_FIELDS}:
            raise ValueError("Response cohort binding mismatch")
        for key in COHORT_FIELDS:
            if key == "archetype" and record[key] is None:
                continue
            if not isinstance(record[key], str) or not record[key]:
                raise ValueError("Invalid response cohort identity")
        for group in ("predicted", "observed"):
            for key in TARGETS:
                if record[group][key] is not None:
                    _number(record[group][key])
        contract = record.get("response_prediction_contract")
        if contract is not None:
            expected = {
                "schema_version": "response-prediction-contract.v1",
                "measurement_scope": "provider_response", "aggregation": "mean_per_completed_response",
                "targets": list(TARGETS),
                **{key: record[key] for key in ("prediction_id", "provider", "model", "model_version", "configuration_hash")},
                **{"predicted_" + key: record["predicted"][key] for key in TARGETS},
            }
            if (not isinstance(contract, dict) or contract != expected
                    or any(type(contract[key]) is not type(value) for key, value in expected.items())):
                raise ValueError("Response prediction scope assertion mismatch")
        if record["eligibility"] == "eligible":
            if (contract is None or record["scope"] != "provider_response"
                    or record["aggregation"] != "mean_per_completed_response"
                    or record["usage_status"] != "complete" or record["execution_status"] != "completed"
                    or type(record["questions_completed"]) is not int or record["questions_completed"] < 1
                    or type(record["questions_count"]) is not int
                    or record["questions_completed"] != record["questions_count"]):
                raise ValueError("Ineligible response usage incorrectly marked eligible")
            for key in TARGETS:
                if _number(record["predicted"][key]) <= 0 or _number(record["observed"][key]) <= 0:
                    raise ValueError("Eligible response targets must be positive")
                total = _number(record["observed"][key.removesuffix("_mean")])
                if not math.isclose(total, record["observed"][key] * record["questions_completed"], rel_tol=1e-12):
                    raise ValueError("Response target aggregation mismatch")
            if _time(record["ended_at"]) < _time(record["started_at"]):
                raise ValueError("Response timestamp order mismatch")
        return record
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("Incomplete response calibration evidence") from exc


def _select(records, cohort):
    if not isinstance(cohort, dict) or set(cohort) != set(COHORT_FIELDS):
        raise ValueError("An exact response model/configuration cohort is required")
    unique = {}
    for record in records:
        _verified(record)
        key = record["observation_id"]
        if key in unique and unique[key] != record:
            raise ValueError("Conflicting response observation replay")
        unique[key] = record
    selected = [record for record in unique.values() if record["cohort"] == cohort and record["eligibility"] == "eligible"]
    selected.sort(key=lambda row: (_time(row["ended_at"]), row["observation_id"]))
    seen = {key: set() for key in INDEPENDENCE_FIELDS}
    independent = []
    for record in selected:
        if any(str(record[key]) in seen[key] for key in INDEPENDENCE_FIELDS):
            continue
        independent.append(record)
        for key in INDEPENDENCE_FIELDS:
            seen[key].add(str(record[key]))
    return independent, len(unique) - len(independent)


def fit_response_candidate(records, *, cohort: dict, target: str = "output_tokens_mean") -> dict:
    """Fit existing Tier-2 regression helpers without modifying history/receipts."""
    if target not in TARGETS:
        raise ValueError("Unsupported response calibration target")
    records, excluded = _select(records, cohort)
    factors, status = None, "insufficient_samples"
    if len(records) >= MIN_SAMPLES:
        predicted = np.array([record["predicted"][target] for record in records], dtype=float)
        actual = np.array([record["observed"][target] for record in records], dtype=float)
        if float(np.ptp(predicted)) <= np.finfo(float).eps * max(1.0, float(np.max(predicted))) * 100:
            status = "degenerate_predictors"
        else:
            fitted = _fit_linear(predicted, actual)
            factors = asdict(fitted)
            if not all(math.isfinite(value) for value in factors.values()):
                raise ValueError("Response calibration fit is not finite")
            status = "ready" if fitted.is_usable and fitted.slope > 0 else "poor_fit"
    return _seal({
        "schema_version": "response-calibration-candidate.v1",
        "classification": "modeled_advisory", "status": status,
        "cohort": dict(cohort), "target": target, "sample_count": len(records),
        "excluded_record_count": excluded, "minimum_samples": MIN_SAMPLES, "factors": factors,
        "training_observations": [{key: record[key] for key in REFERENCE_FIELDS} for record in records],
        "trained_through": max((_time(record["ended_at"]) for record in records)).isoformat() if records else None,
        "calibration_applied": False, "historical_forecast_mutated": False,
        "operational_promotion": False, "quality_acceptance": False,
    }, "candidate")


def _candidate(value):
    try:
        if (value["schema_version"] != "response-calibration-candidate.v1"
                or value["content_hash"] != _hash({k: v for k, v in value.items() if k != "content_hash"})
                or value["candidate_id"] != "candidate-" + _hash({
                    k: v for k, v in value.items() if k not in ("content_hash", "candidate_id")
                })):
            raise ValueError("Response candidate content integrity check failed")
        factors = CalibrationFactors(**value["factors"]) if value["factors"] else None
        if (value["status"] != "ready" or factors is None or not factors.is_usable
                or factors.slope <= 0 or value["sample_count"] < MIN_SAMPLES
                or len(value["training_observations"]) != value["sample_count"]
                or factors.sample_count != value["sample_count"]
                or value["target"] not in TARGETS):
            raise ValueError("Response candidate is not usable")
        if any(value[key] is not False for key in (
            "calibration_applied", "historical_forecast_mutated", "operational_promotion", "quality_acceptance",
        )):
            raise ValueError("Advisory candidate cannot assert applied calibration or authority")
        for key in INDEPENDENCE_FIELDS:
            if len({str(record[key]) for record in value["training_observations"]}) != value["sample_count"]:
                raise ValueError("Response candidate has overlapping training records")
        if not all(type(number) in (int, float) and math.isfinite(number) for number in asdict(factors).values()):
            raise ValueError("Response calibration fit is not finite")
        return factors
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("Invalid response candidate evidence") from exc


def forecast_response(candidate: dict, *, predicted_mean, cohort: dict) -> dict:
    """Create a NEW advisory scoped estimate, never mutate a historical forecast."""
    factors = _candidate(candidate)
    if cohort != candidate["cohort"]:
        raise ValueError("New response forecast must use the exact trained model/configuration cohort")
    if _number(predicted_mean) <= 0:
        raise ValueError("New response predictor must be positive")
    corrected = _number(Calibrator._apply(predicted_mean, factors))
    return _seal({
        "schema_version": "response-advisory-forecast.v1", "classification": "modeled_advisory",
        "candidate_id": candidate["candidate_id"], "candidate_hash": candidate["content_hash"],
        "cohort": dict(cohort), "target": candidate["target"],
        "original_predicted_mean": predicted_mean, "predicted_mean": corrected,
        "calibration_applied": True, "historical_forecast_mutated": False,
        "operational_promotion": False, "quality_acceptance": False,
        "held_out_improvement_proven": False,
    }, "forecast")


def evaluate_response_candidate(candidate: dict, records) -> dict:
    """Evaluate original and candidate on later independent, exact-cohort actuals."""
    factors = _candidate(candidate)
    records = list(records)
    training = candidate["training_observations"]
    seen = {key: {str(record[key]) for record in training} for key in INDEPENDENCE_FIELDS}
    cutoff = _time(candidate["trained_through"])
    for record in records:
        _verified(record)
        if any(str(record[key]) in seen[key] for key in INDEPENDENCE_FIELDS):
            raise ValueError("Training/held-out prediction, receipt or source evidence overlap")
        if record["eligibility"] == "eligible" and _time(record["started_at"]) <= cutoff:
            raise ValueError("Held-out observations must be later than all training evidence")
    selected, excluded = _select(records, candidate["cohort"])
    before, after, status = None, None, "insufficient_held_out_samples"
    if len(selected) >= MIN_HELD_OUT_SAMPLES:
        target = candidate["target"]
        actual = [record["observed"][target] for record in selected]
        original = [record["predicted"][target] for record in selected]
        corrected = [_number(Calibrator._apply(value, factors)) for value in original]
        denominator = _number(sum(actual))
        if denominator == 0:
            raise ValueError("WAPE is undefined for zero observed target usage")
        before = _number(sum(abs(p - a) for p, a in zip(original, actual)) / denominator)
        after = _number(sum(abs(p - a) for p, a in zip(corrected, actual)) / denominator)
        status = "unchanged" if math.isclose(before, after, rel_tol=1e-12, abs_tol=1e-12) else (
            "improved" if after < before else "worse"
        )
    return _seal({
        "schema_version": "response-calibration-evaluation.v1", "classification": "held_out_advisory",
        "candidate_id": candidate["candidate_id"], "candidate_hash": candidate["content_hash"],
        "cohort": dict(candidate["cohort"]), "target": candidate["target"], "status": status,
        "held_out_sample_count": len(selected), "minimum_held_out_samples": MIN_HELD_OUT_SAMPLES,
        "excluded_record_count": excluded,
        "held_out_observations": [{key: record[key] for key in REFERENCE_FIELDS} for record in selected],
        "metric": "weighted_absolute_percentage_error", "metric_unit": "ratio",
        "before_wape": before, "after_wape": after,
        "calibration_applied": False, "historical_forecast_mutated": False,
        "operational_promotion": False, "quality_acceptance": False,
    }, "evaluation")
