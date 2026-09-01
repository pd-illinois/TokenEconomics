"""Split learning evidence without cross-meter reinterpretation."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

LEARNING_EVIDENCE_SCHEMA_VERSION = "learning-evidence.v1"


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def weighted_absolute_percentage_error(
    forecasts: Iterable[float],
    actuals: Iterable[float],
) -> float:
    predicted = [float(value) for value in forecasts]
    observed = [float(value) for value in actuals]
    if len(predicted) != len(observed) or not observed:
        raise ValueError("forecasts and actuals must have the same non-zero length")
    if any(not math.isfinite(value) or value < 0 for value in predicted + observed):
        raise ValueError("forecast and actual values must be finite and non-negative")
    denominator = sum(observed)
    if denominator == 0:
        raise ValueError("WAPE is undefined when all actuals are zero")
    return sum(abs(left - right) for left, right in zip(predicted, observed)) / denominator


def build_learning_proof(
    *,
    reconciliation_reference: Mapping[str, str],
    before_forecast_reference: Mapping[str, str],
    after_forecast_reference: Mapping[str, str],
    before_forecasts: Iterable[float],
    after_forecasts: Iterable[float],
    actuals: Iterable[float],
    predictor_write_reference: Mapping[str, Any],
    commercial_calibration_reference: Mapping[str, Any],
    quality_calibration_reference: Mapping[str, Any],
) -> dict[str, Any]:
    actual_values = list(actuals)
    before_values = list(before_forecasts)
    after_values = list(after_forecasts)
    before_error = weighted_absolute_percentage_error(before_values, actual_values)
    after_error = weighted_absolute_percentage_error(after_values, actual_values)
    proof = {
        "schema_version": LEARNING_EVIDENCE_SCHEMA_VERSION,
        "metric": "weighted_absolute_percentage_error",
        "reconciliation_reference": dict(reconciliation_reference),
        "before_forecast_reference": dict(before_forecast_reference),
        "after_forecast_reference": dict(after_forecast_reference),
        "sample_count": len(actual_values),
        "before_error": before_error,
        "after_error": after_error,
        "change": after_error - before_error,
        "result": (
            "improved"
            if after_error < before_error
            else "unchanged"
            if after_error == before_error
            else "not_improved"
        ),
        "predictor_write_reference": dict(predictor_write_reference),
        "commercial_calibration_reference": dict(commercial_calibration_reference),
        "quality_calibration_reference": dict(quality_calibration_reference),
        "boundaries": {
            "predictor": "model-token and workload actuals only",
            "commercial": "native meters, entitlements, purchase allocation, seats, and resources",
            "quality": "explicit accepted-task outcomes only",
        },
        "historical_forecast_mutated": False,
    }
    proof["content_hash"] = _hash(proof)
    return proof


class IdempotentLearningStore:
    """Persist a write receipt before returning success on any calibration boundary."""

    def __init__(self, root: str | Path, boundary: str) -> None:
        if boundary not in {"predictor", "commercial", "quality"}:
            raise ValueError("invalid learning boundary")
        self.root = Path(root)
        self.boundary = boundary

    def record(
        self,
        *,
        reconciliation_hash: str,
        observations: object,
        writer: Callable[[object], Mapping[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        key = _hash(
            {
                "schema_version": LEARNING_EVIDENCE_SCHEMA_VERSION,
                "boundary": self.boundary,
                "reconciliation_hash": reconciliation_hash,
                "observations": observations,
            }
        )
        path = self.root / f"{key}.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")), False
        outcome = dict(writer(observations))
        record = {
            "schema_version": LEARNING_EVIDENCE_SCHEMA_VERSION,
            "boundary": self.boundary,
            "reconciliation_hash": reconciliation_hash,
            "observation_hash": _hash(observations),
            "writer_outcome": outcome,
            "idempotency_key": key,
        }
        record["content_hash"] = _hash(record)
        temporary = self.root / f".{key}.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2, allow_nan=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return record, True
