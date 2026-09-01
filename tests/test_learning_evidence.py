from __future__ import annotations

from pathlib import Path

from costgov.learning_evidence import (
    IdempotentLearningStore,
    build_learning_proof,
    weighted_absolute_percentage_error,
)


def test_learning_proof_reports_improvement_without_rewriting_history() -> None:
    proof = build_learning_proof(
        reconciliation_reference={"id": "r1", "content_hash": "a" * 64},
        before_forecast_reference={"id": "f1", "content_hash": "b" * 64},
        after_forecast_reference={"id": "f2", "content_hash": "c" * 64},
        before_forecasts=[10, 20],
        after_forecasts=[12, 18],
        actuals=[12, 18],
        predictor_write_reference={"id": "p1"},
        commercial_calibration_reference={"id": "c1"},
        quality_calibration_reference={"id": "q1"},
    )
    assert weighted_absolute_percentage_error([10, 20], [12, 18]) > 0
    assert proof["result"] == "improved"
    assert proof["historical_forecast_mutated"] is False


def test_learning_boundary_writer_runs_once_across_restarts(tmp_path: Path) -> None:
    calls: list[object] = []

    def writer(observations: object) -> dict:
        calls.append(observations)
        return {"status": "recorded"}

    first, created = IdempotentLearningStore(tmp_path, "predictor").record(
        reconciliation_hash="a" * 64,
        observations={"model_tokens": 100},
        writer=writer,
    )
    second, created_again = IdempotentLearningStore(tmp_path, "predictor").record(
        reconciliation_hash="a" * 64,
        observations={"model_tokens": 100},
        writer=writer,
    )
    assert created is True
    assert created_again is False
    assert first == second
    assert len(calls) == 1
