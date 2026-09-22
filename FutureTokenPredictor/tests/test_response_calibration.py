"""Deterministic, synthetic proof of isolated response candidate mechanics."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from future_token_predictor.history.response_calibration import (
    evaluate_response_candidate, fit_response_candidate, forecast_response,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


COHORT = {
    "scope": "provider_response", "aggregation": "mean_per_completed_response",
    "provider": "azure_openai", "model": "model-test", "model_version": "v1",
    "archetype": "rag_pipeline", "configuration_hash": digest({"config": "v1"}),
}


def seal(record):
    record["content_hash"] = digest({k: v for k, v in record.items() if k != "content_hash"})
    return record


def sample(index, *, multiplier=2, predicted=None):
    predicted = predicted if predicted is not None else 10 * (index + 1)
    timestamp = (datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(hours=index)).isoformat()
    value = {
        "schema_version": "response-usage-observation.v1",
        **COHORT, "cohort": dict(COHORT),
        "prediction_id": index, "receipt_id": f"receipt-{index}", "receipt_hash": digest({"receipt": index}),
        "run_id": f"run-{index}", "batch_hash": digest({"batch": index}),
        "started_at": timestamp, "ended_at": timestamp,
        "eligibility": "eligible", "usage_status": "complete", "execution_status": "completed",
        "questions_count": 2, "questions_completed": 2,
        "predicted": {"input_tokens_mean": predicted * 2, "output_tokens_mean": predicted},
        "observed": {"input_tokens_mean": predicted * multiplier * 2, "output_tokens_mean": predicted * multiplier,
                     "input_tokens": predicted * multiplier * 4, "output_tokens": predicted * multiplier * 2},
        "response_prediction_contract": {
            "schema_version": "response-prediction-contract.v1",
            "measurement_scope": "provider_response", "aggregation": "mean_per_completed_response",
            "targets": ["input_tokens_mean", "output_tokens_mean"],
            "prediction_id": index, "provider": COHORT["provider"], "model": COHORT["model"],
            "model_version": COHORT["model_version"], "configuration_hash": COHORT["configuration_hash"],
            "predicted_input_tokens_mean": predicted * 2, "predicted_output_tokens_mean": predicted,
        },
    }
    value["observation_id"] = "response-" + digest({
        key: value[key] for key in ("prediction_id", "receipt_id", "receipt_hash", "run_id", "batch_hash")
    })
    return seal(value)


def fit(multiplier=2):
    return fit_response_candidate([sample(i, multiplier=multiplier) for i in range(10)], cohort=COHORT)


def test_varied_independent_cohort_fits_without_legacy_database():
    records = [sample(i) for i in range(10)]
    before = copy.deepcopy(records)
    candidate = fit_response_candidate(records, cohort=COHORT)
    assert candidate["status"] == "ready"
    assert candidate["sample_count"] == 10
    assert candidate["factors"]["slope"] == pytest.approx(2)
    assert candidate["factors"]["r_squared"] == pytest.approx(1)
    forecast = forecast_response(candidate, predicted_mean=125, cohort=COHORT)
    assert forecast["predicted_mean"] == pytest.approx(250)
    assert forecast["classification"] == "modeled_advisory"
    assert forecast["operational_promotion"] is False
    assert records == before


@pytest.mark.parametrize("training,heldout,expected", [(2, 2, "improved"), (1, 1, "unchanged"), (2, 1, "worse")])
def test_heldout_improvement_unchanged_and_worse_are_reported_honestly(training, heldout, expected):
    result = evaluate_response_candidate(fit(training), [sample(i, multiplier=heldout) for i in range(10, 13)])
    assert result["status"] == expected
    assert result["held_out_sample_count"] == 3
    assert result["before_wape"] is not None and result["after_wape"] is not None
    assert result["calibration_applied"] is False
    assert result["operational_promotion"] is False


def test_insufficient_training_and_heldout_do_not_manufacture_proof():
    candidate = fit_response_candidate([sample(i) for i in range(9)], cohort=COHORT)
    assert candidate["status"] == "insufficient_samples"
    with pytest.raises(ValueError):
        forecast_response(candidate, predicted_mean=100, cohort=COHORT)
    result = evaluate_response_candidate(fit(), [sample(11)])
    assert result["status"] == "insufficient_held_out_samples"
    assert result["before_wape"] is result["after_wape"] is None


def test_replay_and_reused_prediction_batches_count_once():
    records = [sample(0)] * 12
    reused = sample(1)
    reused["prediction_id"] = 0
    reused["response_prediction_contract"]["prediction_id"] = 0
    reused["observation_id"] = "response-" + digest({
        key: reused[key] for key in ("prediction_id", "receipt_id", "receipt_hash", "run_id", "batch_hash")
    })
    candidate = fit_response_candidate(records + [seal(reused)], cohort=COHORT)
    assert candidate["sample_count"] == 1
    assert candidate["status"] == "insufficient_samples"


def test_degenerate_predictors_and_poor_fit_are_not_usable():
    candidate = fit_response_candidate([sample(i, predicted=100) for i in range(10)], cohort=COHORT)
    assert candidate["status"] == "degenerate_predictors"
    records = [sample(i, multiplier=1 / (i + 1)) for i in range(10)]
    assert fit_response_candidate(records, cohort=COHORT)["status"] == "poor_fit"


@pytest.mark.parametrize("field", ["model", "model_version", "configuration_hash", "scope"])
def test_incomparable_cohort_excluded_and_wrong_new_forecast_rejected(field):
    wrong = dict(COHORT, **{field: "other"})
    assert fit_response_candidate([sample(i) for i in range(10)], cohort=wrong)["sample_count"] == 0
    with pytest.raises(ValueError):
        forecast_response(fit(), predicted_mean=100, cohort=wrong)


@pytest.mark.parametrize("target", [None, 0])
def test_missing_zero_targets_are_not_samples(target):
    record = sample(0)
    record["observed"]["output_tokens_mean"] = target
    record["observed"]["output_tokens"] = target
    record["eligibility"] = "target_usage_unavailable"
    candidate = fit_response_candidate([seal(record)] * 10, cohort=COHORT)
    assert candidate["sample_count"] == 0


def test_scope_boolean_and_corruption_never_become_samples():
    value = sample(0)
    value["response_prediction_contract"] = True
    with pytest.raises(ValueError):
        fit_response_candidate([seal(value)], cohort=COHORT)
    value = sample(0)
    value["observed"]["output_tokens_mean"] = 999
    with pytest.raises(ValueError):
        fit_response_candidate([value], cohort=COHORT)


def test_train_test_overlap_and_earlier_observations_rejected():
    candidate = fit()
    with pytest.raises(ValueError, match="overlap"):
        evaluate_response_candidate(candidate, [sample(0)])
    value = sample(10)
    value["started_at"] = sample(0)["started_at"]
    with pytest.raises(ValueError, match="later"):
        evaluate_response_candidate(candidate, [seal(value)])


def test_new_batch_reusing_training_prediction_rejected():
    value = sample(12)
    value["prediction_id"] = 3
    value["response_prediction_contract"]["prediction_id"] = 3
    value["observation_id"] = "response-" + digest({
        key: value[key] for key in ("prediction_id", "receipt_id", "receipt_hash", "run_id", "batch_hash")
    })
    with pytest.raises(ValueError, match="overlap"):
        evaluate_response_candidate(fit(), [seal(value)])


def test_rehashed_contract_with_wrong_model_rejected():
    value = sample(11)
    value["response_prediction_contract"]["model"] = "wrong"
    with pytest.raises(ValueError):
        evaluate_response_candidate(fit(), [seal(value)])


def test_tampered_candidate_and_nonfinite_new_forecast_rejected():
    candidate = fit()
    candidate["factors"]["slope"] = 100
    with pytest.raises(ValueError):
        forecast_response(candidate, predicted_mean=100, cohort=COHORT)
    with pytest.raises(ValueError):
        forecast_response(fit(), predicted_mean=True, cohort=COHORT)
