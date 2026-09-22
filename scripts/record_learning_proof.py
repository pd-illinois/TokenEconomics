"""Persist split reconciliation-to-learning receipts and a forecast-error proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "FutureTokenPredictor" / "src"))

from costgov.learning_evidence import (
    IdempotentLearningStore,
    LearningEvidenceStore,
    build_learning_proof,
)
from costgov.observe_economics import load_verified_run_evidence
from costgov.reconciliation_evidence import ReconciliationEvidenceStore
from costgov.reports import ReportStore
from future_token_predictor.history import HistoryDatabase


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reconciliation-id", required=True)
    parser.add_argument("--before-prediction-id", type=int, required=True)
    parser.add_argument("--after-prediction-id", type=int, required=True)
    return parser.parse_args()


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _forecast_snapshot(record: Any) -> dict[str, Any]:
    return {
        "prediction_id": record.id,
        "model": record.model,
        "provider": record.provider,
        "archetype": record.archetype,
        "complexity": record.complexity,
        "predicted_text_input": record.predicted_text_input,
        "predicted_text_output": record.predicted_text_output,
        "predicted_document_input": record.predicted_document_input,
        "predicted_total": record.predicted_total,
        "predicted_cost": record.predicted_cost,
        "prediction_method": record.prediction_method,
    }


def main() -> int:
    args = _parse_args()
    run_root = ROOT / "studio_runs" / args.run_id
    run_result = json.loads((run_root / "result.json").read_text(encoding="utf-8"))
    if run_result.get("status") != "completed":
        raise ValueError("learning requires a completed run")

    reconciliations = ReconciliationEvidenceStore(
        ROOT / "studio_reconciliation_evidence"
    ).list()
    reconciliation = next(
        (
            item
            for item in reconciliations
            if item["reconciliation_id"] == args.reconciliation_id
            and item["run_id"] == args.run_id
        ),
        None,
    )
    if reconciliation is None:
        raise ValueError("matching reconciliation evidence was not found")

    trajectories, outcomes, entries = load_verified_run_evidence(run_result, run_root)
    task_ids = {item.task.task_id for item in trajectories}
    token_entries = [
        item
        for item in entries
        if item.meter_family == "direct_token" and item.task_id in task_ids
    ]
    if len(token_entries) != len(task_ids):
        raise ValueError("learning requires one measured direct-token entry per task")
    actuals = [float(item.quantity) for item in token_entries]
    if any(not math.isfinite(value) or value < 0 for value in actuals):
        raise ValueError("measured task token quantities must be finite and non-negative")

    db = HistoryDatabase()
    before = db.get_record(args.before_prediction_id)
    after = db.get_record(args.after_prediction_id)
    if before is None or after is None:
        raise ValueError("both predictor history records are required")
    before_snapshot = _forecast_snapshot(before)
    after_snapshot = _forecast_snapshot(after)
    if (
        before.model,
        before.provider,
        before.archetype,
        before.complexity,
    ) != (
        after.model,
        after.provider,
        after.archetype,
        after.complexity,
    ):
        raise ValueError("before and after forecasts must have the same calibration cohort")

    measured_mean = sum(actuals) / len(actuals)
    if before.actual_total is None or not math.isclose(
        before.actual_total, measured_mean, rel_tol=1e-9
    ):
        raise ValueError("before prediction actual does not match measured run evidence")

    receipt_root = ROOT / "studio_learning_evidence" / "receipts"
    predictor_observations = {
        "run_id": args.run_id,
        "prediction_id": before.id,
        "task_count": len(actuals),
        "measured_total_tokens": sum(actuals),
        "measured_mean_tokens_per_task": measured_mean,
        "measurement_scope": "provider-reported complete Foundry response per task",
        "reconciliation_hash": reconciliation["content_hash"],
    }

    def write_predictor(_: object) -> dict[str, Any]:
        status = db.record_actual(before.id, actual_total=measured_mean)
        sample_count = db.count_calibration_records(before.model, before.archetype)
        return {
            "status": status,
            "prediction_id": before.id,
            "calibration_cohort": f"{before.model}:{before.archetype}",
            "calibration_sample_count": sample_count,
            "calibration_applied_to_after_forecast": (
                after.prediction_method == "tier2_calibrated"
            ),
            "reason": (
                "Tier 2 remains unavailable until the predictor has enough "
                "non-degenerate matched historical samples."
            ),
        }

    predictor_receipt, _ = IdempotentLearningStore(
        receipt_root / "predictor", "predictor"
    ).record(
        reconciliation_hash=reconciliation["content_hash"],
        observations=predictor_observations,
        writer=write_predictor,
    )

    commercial_observations = {
        "reconciliation_id": reconciliation["reconciliation_id"],
        "billing_status": reconciliation["status"],
        "billing_row_count": reconciliation["billing"]["row_count"],
        "billing_total": reconciliation["billing"]["total_cost"],
        "billing_currencies": reconciliation["billing"]["currencies"],
        "missing_resource_ids": reconciliation["billing"]["missing_resource_ids"],
        "claim": "Subscription billing remains independent from per-task model calibration.",
    }
    commercial_receipt, _ = IdempotentLearningStore(
        receipt_root / "commercial", "commercial"
    ).record(
        reconciliation_hash=reconciliation["content_hash"],
        observations=commercial_observations,
        writer=lambda _: {
            "status": "recorded_as_independent_commercial_evidence",
            "predictor_tokens_reinterpreted": False,
        },
    )

    quality_by_segment: dict[str, dict[str, int]] = {}
    for trajectory in trajectories:
        quality_by_segment.setdefault(
            trajectory.task.segment.segment_id,
            {"completed": 0, "accepted": 0},
        )["completed"] += 1
    for outcome in outcomes:
        segment = next(
            item.task.segment.segment_id
            for item in trajectories
            if item.task.task_id == outcome.task_id
        )
        if outcome.decision.value == "accepted":
            quality_by_segment[segment]["accepted"] += 1
    quality_observations = {
        "run_id": args.run_id,
        "segments": quality_by_segment,
        "acceptance_reference_count": len(outcomes),
        "claim": "Explicit accepted-task outcomes are not predictor token actuals.",
    }
    quality_receipt, _ = IdempotentLearningStore(
        receipt_root / "quality", "quality"
    ).record(
        reconciliation_hash=reconciliation["content_hash"],
        observations=quality_observations,
        writer=lambda _: {
            "status": "recorded_as_independent_quality_evidence",
            "segment_controls_preserved": True,
        },
    )

    proof = build_learning_proof(
        reconciliation_reference={
            "id": reconciliation["reconciliation_id"],
            "content_hash": reconciliation["content_hash"],
        },
        before_forecast_reference={
            "id": str(before.id),
            "content_hash": _hash(before_snapshot),
        },
        after_forecast_reference={
            "id": str(after.id),
            "content_hash": _hash(after_snapshot),
        },
        before_forecasts=[before.predicted_total] * len(actuals),
        after_forecasts=[after.predicted_total] * len(actuals),
        actuals=actuals,
        predictor_write_reference=predictor_receipt,
        commercial_calibration_reference=commercial_receipt,
        quality_calibration_reference=quality_receipt,
    )
    stored, created = LearningEvidenceStore(
        ROOT / "studio_learning_evidence" / "proofs"
    ).append(proof)
    ReportStore(ROOT / "studio_reports").add_artifact(
        run_result["report_id"],
        "learning_proofs",
        {
            "id": stored["learning_proof_id"],
            "content_hash": stored["content_hash"],
            "result": stored["result"],
            "sample_count": stored["sample_count"],
        },
    )
    print(
        json.dumps(
            {
                "learning_proof_id": stored["learning_proof_id"],
                "content_hash": stored["content_hash"],
                "created": created,
                "sample_count": stored["sample_count"],
                "before_error": stored["before_error"],
                "after_error": stored["after_error"],
                "result": stored["result"],
                "predictor_calibration_sample_count": predictor_receipt[
                    "writer_outcome"
                ]["calibration_sample_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
